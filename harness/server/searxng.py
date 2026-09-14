"""SearXNG JSON search. The endpoint is operator configuration, never model input."""
import asyncio
import json
from urllib.parse import urlsplit

import httpx

from .research import ResearchError


async def _query(base_url, params):
    try:
        async with asyncio.timeout(40), httpx.AsyncClient(timeout=35, trust_env=False, follow_redirects=False) as client:
            async with client.stream('GET', base_url.rstrip('/') + '/search', params=params) as response:
                if response.status_code != 200:
                    raise ResearchError(f'SearXNG returned HTTP {response.status_code}; check the service and enable search.formats: [html, json]')
                body = bytearray()
                async for block in response.aiter_bytes(65536):
                    body.extend(block)
                    if len(body) > 2 * 1024 * 1024:
                        raise ResearchError('SearXNG response exceeded 2 MiB')
                data = json.loads(body)
        if not isinstance(data, dict) or not isinstance(data.get('results'), list):
            raise ValueError('Invalid results')
    except (httpx.HTTPError, TimeoutError):
        raise ResearchError('SearXNG is unavailable; check the search container and retry', code='search_unavailable') from None
    except (ValueError, TypeError) as error:
        if isinstance(error, ResearchError):
            raise
        raise ResearchError('SearXNG did not return a valid JSON search response') from None
    return data


def engine_warnings(data):
    """Unresponsive engines as readable warnings: SearXNG reports [name, reason] pairs."""
    warnings = []
    for row in (data.get('unresponsive_engines') or [])[:12]:
        if isinstance(row, (list, tuple)) and len(row) >= 2:
            message = f'{row[0]}: {row[1]}'
        elif isinstance(row, dict):
            message = ': '.join(str(v) for v in row.values())
        else:
            message = str(row)
        warnings.append({'code': 'engine_unavailable', 'message': message[:300]})
    return warnings


async def search(base_url, query, *, images=False, retry_pause=2.0):
    parts = urlsplit(base_url)
    if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise ResearchError('Invalid SearXNG server URL in research configuration')
    params = {'q': query, 'format': 'json', 'categories': 'images' if images else 'general'}
    data = await _query(base_url, params)
    if not data['results'] and data.get('unresponsive_engines'):
        # Rate-limited or timed-out engines usually answer a moment later; one retry
        # saves the model a turn of rephrasing a query that was fine.
        await asyncio.sleep(retry_pause)
        data = await _query(base_url, params)
    rows = []
    for item in data['results'][:40]:
        if not isinstance(item, dict):
            continue
        rows.append({'url': item.get('img_src') if images else item.get('url'),
                     'page': item.get('url'), 'title': str(item.get('title') or ''),
                     'snippet': str(item.get('content') or ''), 'engine': str(item.get('engine') or 'searxng')})
    return 'searxng', rows, engine_warnings(data)
