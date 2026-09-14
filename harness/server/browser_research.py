"""Local Chromium search/read adapted from the user's lm-chat-proxy.js reference.

No subscription service or CORS relay. The UI's agent calls this local tool. Web
content never gets access to the CAD session, browser profile, files or secrets.
"""
import asyncio
import base64
import re
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, quote, urlsplit

from .research import ResearchError, public_url
from .browser_proxy import PublicBrowserProxy

_BROWSER_LIMIT = asyncio.Semaphore(1)
# Order and endpoints were re-verified 2026-09-11: Bing's setmkt variant ignored the
# query and returned the same results for every search; DuckDuckGo's HTML endpoints
# return 403 from this host; Brave's result DOM moved from #results to #mixed-main.
ENGINES = (
    ('brave', 'https://search.brave.com/search?source=web&q='),
    ('bing', 'https://www.bing.com/search?q='),
    ('duckduckgo', 'https://html.duckduckgo.com/html/?kl=us-en&q='),
)
ROWS_PER_ENGINE = 20
TARGET_RESULTS = 12
CHALLENGE = ('unusual traffic', 'are you a robot', 'confirm this search was made by a human',
             'verifying you', 'verify you are human', 'checking your browser', 'just a moment...',
             'enable javascript and cookies to continue', 'press & hold')
BLOCKED_HOSTS = ('doubleclick.net', 'google-analytics.com', 'googletagmanager.com',
                 'googlesyndication.com', 'connect.facebook.net')

# Selectors and engine ordering follow lm-chat-proxy.js; extracted strings are data.
SEARCH_SCRIPT = """engine => {
  const parsers = {
    duckduckgo: {row:'.result:not(.result--ad)', link:'.result__a', snippet:'.result__snippet'},
    bing: {row:'li.b_algo', link:'h2 a', snippet:'.b_caption p, .b_algoSlug'},
    brave: {row:'#mixed-main .snippet[data-type="web"], #results [data-type="web"]', link:'a[href^="http"]', snippet:'.snippet-content, .snippet-description, .generic-snippet'}
  };
  const parser = parsers[engine]; if (!parser) return [];
  const rows = [...document.querySelectorAll(parser.row)];
  return rows.slice(0, 20).flatMap(el => {
    const a = el.querySelector(parser.link);
    if (!a) return [];
    const title = (el.querySelector('.title, .snippet-title') || a).textContent.trim();
    const sn = el.querySelector(parser.snippet);
    return [{url:a.href, title, snippet:sn ? sn.textContent.trim() : ''}];
  });
}"""
READ_SCRIPT = """() => {
  // Capture textContent from a detached clone: innerText can be empty off-document.
  const clone = document.cloneNode(true);
  // Consent dialogs often set aria-hidden on the ENTIRE underlying product page.
  // Remove the dialog, not those background ancestors; this is text extraction,
  // not a visibility or accessibility-tree query. No consent is accepted/clicked.
  clone.querySelectorAll('script,style,noscript,svg,iframe,nav,footer,header,form,aside,[role="dialog"],[aria-modal="true"]').forEach(el => el.remove());
  const candidates = [...clone.querySelectorAll('main,article,[role="main"]')];
  candidates.sort((a,b) => b.textContent.length-a.textContent.length);
  const root = candidates[0] || clone.body;
  root.querySelectorAll('p,div,tr,li,h1,h2,h3,h4,section,br').forEach(el => el.prepend('\\n'));
  root.querySelectorAll('td,th').forEach(el => el.prepend(' | '));
  const text = (root.textContent || '').replace(/[ \\t]+/g,' ').replace(/\\n{3,}/g,'\\n\\n').trim().slice(0,400000);
  // Hub pages repeat identical anchor text ("Mechanical drawings, PDF") under many
  // product headings; the nearest preceding heading tells which product a link belongs to.
  const heads = [...document.querySelectorAll('h1,h2,h3,h4')];
  const headingFor = a => { let best = ''; for (const h of heads) { if (h.compareDocumentPosition(a) & Node.DOCUMENT_POSITION_FOLLOWING) best = h.textContent.trim().slice(0,120); else break; } return best; };
  const links = [...document.querySelectorAll('a[href]')].map(a => ({url:a.href,title:a.textContent.trim().slice(0,160),context:headingFor(a)}))
    .filter(a => a.title && /^https?:/.test(a.url));
  links.sort((a,b) => Number(/spec|drawing|download|technical|dimension|\\.pdf/i.test(b.title+b.url)) - Number(/spec|drawing|download|technical|dimension|\\.pdf/i.test(a.title+a.url)));
  const published = document.querySelector('meta[property="article:published_time"],meta[name="datePublished"],meta[itemprop="datePublished"],time[itemprop="datePublished"]');
  const published_at = published ? (published.content || published.getAttribute('datetime')) : null;
  const headings = [...clone.querySelectorAll('h1,h2,h3')].slice(0,80).map(h=>h.textContent.trim().slice(0,240));
  const dates = new Set(text.match(/\\b20\\d{2}-\\d{2}-\\d{2}\\b/g) || []);
  return {title:document.title.slice(0,1000), text, links:links.slice(0,160), headings, published_at,
    temporal_warning:dates.size>2?'This page mentions several dates; publication and event dates may differ.':null};
}"""


def result_url(raw):
    parts = urlsplit(raw)
    if parts.hostname in ('duckduckgo.com', 'html.duckduckgo.com'):
        raw = parse_qs(parts.query).get('uddg', [raw])[0]
    if parts.hostname == 'www.bing.com' and parts.path == '/ck/a':
        encoded = parse_qs(parts.query).get('u', [''])[0]
        if encoded.startswith('a1'):
            try:
                raw = base64.urlsafe_b64decode(encoded[2:] + '=' * (-len(encoded[2:]) % 4)).decode()
            except (ValueError, UnicodeError):
                raise ResearchError('Invalid search redirect') from None
    return public_url(raw)


def relevant_results(query, rows):
    """Whole title/snippet tokens, never URL substrings. NOT a source-quality score."""
    generic = {'the', 'and', 'for', 'with', 'what', 'are', 'how', 'does', 'official', 'site',
               'specifications', 'specs', 'dimensions', 'size', 'standard', 'mechanical', 'model',
               'mm', 'pcb', 'mounting', 'hole', 'holes', 'spacing', 'diameter', 'positions',
               'position', 'port', 'ports', 'connector', 'connectors', 'locations', 'layout',
               'board', 'case', 'usb', 'hdmi', 'micro', 'gpio', 'exact', 'drawing', 'technical'}
    tokens = lambda text: set(re.findall(r'[a-z0-9]+', text.lower()))
    words = {word for word in tokens(query) if len(word) > 1 and not word.isdigit()}
    anchors = words - generic
    for row in rows:
        found = tokens(row.get('title', '') + ' ' + row.get('snippet', ''))
        if (anchors and len(anchors & found) >= min(2, len(anchors))) or (not anchors and len(words & found) >= min(2, len(words)) and words):
            return True
    return False


@asynccontextmanager
async def local_page(*, scripts=True):
    from playwright.async_api import async_playwright
    async with _BROWSER_LIMIT, PublicBrowserProxy() as proxy, async_playwright() as playwright:
        # Chromium retains its native TLS/network stack; the local proxy pins DNS.
        browser = await playwright.chromium.launch(headless=True, chromium_sandbox=True,
            proxy=proxy.options,
            args=['--disable-quic', '--force-webrtc-ip-handling-policy=disable_non_proxied_udp'])
        context = await browser.new_context(viewport={'width': 1280, 'height': 900}, locale='en-US',
            service_workers='block', accept_downloads=False,
            user_agent=f'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{browser.version} Safari/537.36')
        requests = 0

        async def intercept(route):
            nonlocal requests
            try:
                request = route.request
                host = urlsplit(request.url).hostname or ''
                if any(host == blocked or host.endswith('.' + blocked) for blocked in BLOCKED_HOSTS):
                    return await route.abort()
                allowed = ('document', 'script', 'xhr', 'fetch') if scripts else ('document',)
                if (request.method != 'GET' or request.resource_type not in allowed or request.frame != page.main_frame):
                    return await route.abort()
                requests += 1
                if requests > 80:
                    return await route.abort()
                public_url(request.url)
                await route.continue_()
            except Exception as error:
                # Playwright may already have disposed the route after stop/navigation.
                if route.request.is_navigation_request():
                    page.research_errors.append(str(error) if isinstance(error, ResearchError) else 'request failed')
                try:
                    await route.abort()
                except Exception:
                    pass

        page = await context.new_page()
        page.research_errors = []
        await context.route('**/*', intercept)
        await context.route_web_socket('**/*', lambda socket: socket.close())
        page.set_default_timeout(30000)
        try:
            yield page
        finally:
            await context.close()
            await browser.close()


async def search_attempt(page, name, endpoint, query):
    """One DOM integration attempt; separately testable from fallback policy."""
    response = await page.goto(endpoint + quote(query), wait_until='domcontentloaded', timeout=30000)
    if response and response.status >= 400:
        raise ResearchError(f'HTTP {response.status}', code='http_error')
    await page.wait_for_timeout(600)
    text = (await page.inner_text('body')).lower()[:4000]
    if any(marker in text for marker in CHALLENGE):
        raise ResearchError('access challenge', code='challenge')
    parsed = await page.evaluate(SEARCH_SCRIPT, name)
    rows = []
    for row in parsed:
        try:
            candidate = row | {'url': result_url(row['url'])}
            if relevant_results(query, [candidate]):
                rows.append(candidate)
        except ResearchError:
            continue
    if not rows:
        raise ResearchError('no relevant results', code='no_results')
    return rows[:ROWS_PER_ENGINE]


async def browser_search(query):
    """Merge results from successive engines until TARGET_RESULTS unique pages.

    One engine's top rows are often the same few homepages for any wording of a
    query; a second engine adds different pages, which matters more to the model
    than the ordering of the first.
    """
    warnings, diagnostics, merged, providers, seen = [], [], [], [], set()
    try:
        async with asyncio.timeout(190), local_page(scripts=False) as page:
            for name, endpoint in ENGINES:
                if len(merged) >= TARGET_RESULTS:
                    break
                for attempt in range(1, 3):
                    try:
                        rows = await search_attempt(page, name, endpoint, query)
                        providers.append(name)
                        for row in rows:
                            key = row['url'].rstrip('/').lower()
                            if key not in seen:
                                seen.add(key)
                                merged.append(row | {'engine': name})
                        break
                    except Exception as error:
                        detail = str(error) if isinstance(error, ResearchError) else (page.research_errors[-1] if page.research_errors else 'page unavailable or timed out')
                        warnings.append(f'{name} · attempt {attempt}: {detail}')
                        diagnostics.append({'engine': name, 'attempt': attempt, 'code': getattr(error, 'code', 'navigation_error'), 'message': detail})
                        if getattr(error, 'code', '') in ('http_error', 'no_results'):
                            break  # A refused or empty engine will not change on an identical retry.
    except TimeoutError:
        diagnostics.append({'code': 'timeout', 'message': 'Search time budget exhausted'})
    if merged:
        return '+'.join(providers), merged, warnings
    raise ResearchError('Web search is unavailable. Try another query or a known public URL.',
                        code='search_unavailable', diagnostics=diagnostics)

async def browser_read(url):
    async with asyncio.timeout(35), local_page() as page:
        documents = []
        def observe(response):
            if response.request.resource_type == 'document' and response.request.frame == page.main_frame:
                documents.append(response)
        page.on('response', observe)
        try:
            response = await page.goto(public_url(url), wait_until='domcontentloaded', timeout=20000)
            if response and response.status >= 400:
                raise ResearchError(f'Website returned HTTP {response.status}')
            if response and 'application/pdf' in response.headers.get('content-type', '').lower():
                return {'pdf': True, 'url': public_url(response.url)}
        except ResearchError:
            raise
        except Exception:
            # Chromium can report "Download is starting" instead of a navigation
            # for extensionless datasheet URLs. Never accept HTML error pages as PDFs.
            pdf = next((r for r in reversed(documents) if r.status < 400 and
                        'application/pdf' in r.headers.get('content-type', '').lower()), None)
            if pdf:
                return {'pdf': True, 'url': public_url(pdf.url)}
            raise ResearchError(page.research_errors[-1] if page.research_errors else 'Website navigation failed or timed out') from None
        await page.wait_for_timeout(700)
        result = await page.evaluate(READ_SCRIPT)
        if len(result['text']) < 100 or any(marker in result['text'].lower()[:3000] for marker in CHALLENGE):
            raise ResearchError('Page is unreadable or challenge-blocked; use another public source. No challenge was bypassed.')
        links = []
        for link in result['links']:
            try:
                links.append(link | {'url': public_url(link['url'])})
            except ResearchError:
                continue
        return result | {'url': public_url(page.url), 'links': links}


IMAGE_SEARCH_SCRIPT = """() => {
  const out = [];
  for (const a of document.querySelectorAll('a.iusc')) {
    try { const m = JSON.parse(a.getAttribute('m') || '{}'); if (m.murl) out.push({url: m.murl, title: (m.t || '').slice(0, 200), page: m.purl || null}); } catch (e) {}
    if (out.length >= 24) break;
  }
  return out;
}"""


async def browser_image_search(query):
    """Bing image search parsed from its result markup (no API); returns image URLs only."""
    async with asyncio.timeout(90), local_page(scripts=False) as page:
        response = await page.goto('https://www.bing.com/images/search?q=' + quote(query) + '&form=HDRSC2', wait_until='domcontentloaded', timeout=30000)
        if response and response.status >= 400:
            raise ResearchError(f'HTTP {response.status}', code='http_error')
        await page.wait_for_timeout(700)
        text = (await page.inner_text('body')).lower()[:4000]
        if any(marker in text for marker in CHALLENGE):
            raise ResearchError('access challenge', code='challenge')
        rows = await page.evaluate(IMAGE_SEARCH_SCRIPT)
        results = []
        for row in rows:
            try:
                results.append(row | {'url': public_url(row['url'])})
            except ResearchError:
                continue
        if not results:
            raise ResearchError('no image results', code='no_results')
        return results
