# CADPilot conversation experience

Implemented September 11, 2026, in the existing FastAPI + vanilla ES-module app.
No renderer bundle, frontend framework, hosted search API, or training UI was added.

This describes the initial UI pass. The later [conversation-continuity update](chat-continuity-2026-09-11.md)
supersedes its user-only model memory, thinking-disabled setup, and default System
theme, and adds live provider reasoning and typed CAD-input streaming.

## What changed

- Warm neutral light/dark/system themes, a quieter run summary, readable serif
  answers, an always-available composer, and mobile Chat/Canvas switching.
- Continuous assistant turns with stable thinking, research and CAD activity
  entries. The 20px rail connects related operations. Older activity collapses to
  the latest two entries; deliberately opened details are retained.
- Compact, bounded source lists with keyboard-accessible detail disclosures,
  full titles, actual snippets, domain, known publication date, retrieval time,
  optional hostname-based classification, and discovered/opened distinction.
- Separate page visits with requested URL, real title, external link, extracted
  text preview, failure/cancellation state and diagnostics. A failed read does not
  remove successful search results.
- Incremental public answers, real-source citation buttons and source previews.
  References preserve whether the evidence was a search result or an opened page
  at the time of the answer. Unknown source IDs are plain text, never citations.
- Restrained entrance/reveal/collapse motion; only running operations keep moving.
  Both OS reduced motion and the appearance menu are respected.
- Follow near the bottom, otherwise preserve a reading anchor and text selection;
  Jump to latest is explicit. Disclosures do not force scrolling. Escape closes
  a source preview/settings before it can trigger the existing Stop shortcut.
- Web search toggle in Tools & appearance, enforced in both advertised planning
  schemas and execution, including cached results. It persists per project.

## Architecture and records

`harness/web/chat/state.js` is the explicit, JSDoc-typed reducer. Turns, displayable
status segments, operations, results, page visits, answers and source references
are distinct records. Global event IDs make duplicate delivery idempotent. Tool
IDs and parent turn IDs survive steering. Offset-based answer fragments handle
duplicates/out-of-order arrival after answer start. Cancelled operations reject
late success. A reopened interrupted operation does not animate as active or
become a successful operation.

`components.js` contains AssistantTurn, ActivityTimeline, TimelineEntry,
SearchResults, SourceRow, SourceDetails and StreamingAnswer with shared accessible
DOM primitives. `panel.js` reconciles keyed components and owns scroll behavior.
`chat.css` contains the conversation design/motion system. The original session,
CAD viewport, desktop controls, model panel, export and project flows remain in
`app.js`; its old flat chat-card renderer was removed.

The answer renderer appends nodes, with inline emphasis/code and bound citations;
it does not rerender old paragraphs or run model HTML. It is not a complete
CommonMark/table/math renderer. Source icons use a fixed-size generic globe when
no trusted local raster favicon is supplied. We intentionally do not add remote
favicon services/beacons or loosen the app CSP.

`server/chat_stream.py` decodes only the planner's public `message` field, after
a complete `respond`/`ask` decision is available. `agent.py` streams genuine
provider SSE deltas. Split JSON escapes and Unicode are buffered safely. Full
plan validation still precedes any tool action. Duplicate JSON keys, incomplete
streams and truncated completions fail explicitly; partial answers remain marked
failed/cancelled. Providers which ignore `stream` return one complete response;
we do not manufacture timed fragments. Kernel-generated save acknowledgments are
also delivered as complete messages, not fake token streams.

Planning is configured with thinking disabled. The UI shows real execution status
and supplied public summaries, not invented internal reasoning. Its explicit
thinking event contract is covered by a supplied-summary fixture.

`server/transcript.py` commits UI events to `project/chat.sqlite3` before broadcast.
Snapshots replay durable history separately from the bounded runtime event buffer.
This covers tool results, errors, cancellations, source metadata and answer deltas.
Full CAD recipes and screenshots are not duplicated into this transcript. The
existing activity journal/export remains available. Conversations are also saved
at submission, steering and research checkpoints, not just normal shutdown.

**UI persistence is not model-memory replay.** The model continues to receive the
existing user conversation, bounded execution/feedback history, source evidence
and quote-validated notes (full text for the latest four fetched sources). We do
not inject the entire UI transcript as prior structured provider tool messages.
Repeated search cannot demote fetched evidence to a snippet. When page content
changes, previously accepted quotes are rechecked and unsupported facts removed.

Old projects without a chat database retain their saved model/research and existing
conversation context. Missing pre-upgrade operation history is not synthesized.
Active legacy backends still render their existing event stream, but must restart
to get durable chat history, streaming and the composer web setting.

## Local research behavior and boundaries

The existing Python Playwright integration remains the implementation, not the
reference project's Node/Electron child process. Searches are sequential:
DuckDuckGo HTML, Bing, Brave; up to two attempts per engine, returning the first
successful engine, at most eight results. Parser selectors are engine-specific
and tested against deterministic DOMs. Redirect targets are normalized. Failed
attempts preserve engine/attempt/error diagnostics; no fallback results are invented.

Browser contexts are fresh and disposable rather than persistent personal or
shared profiles. No stealth/challenge bypass is added. Public HTTP(S), credential
rejection, DNS validation and an authenticated local IP-pinning network proxy are
retained. Browser interception also blocks non-read requests, frames, downloads,
websockets and selected tracking hosts. Stop/steering/web-disable cancel the task,
HTTP model read, browser operation and context/proxy cleanup. Real Chromium close
on cancellation is tested, not inferred merely from a client AbortSignal.

Query/URL caps are 2,048/8,192 characters. Search navigation timeout is 30 seconds;
fallback has a total 190-second ceiling. Existing HTML/PDF extraction remains
bounded and heuristic, not semantic ranking, image/OCR or exact-variant validation.
Publication metadata, up to 80 headings and multi-date warnings are retained.
Normalized per-tool JSON is capped at approximately 24,000 characters by trimming
fields/optional metadata or omitting sources with explicit truncation metadata,
never slicing a serialized JSON document. Requested URLs and source IDs are not
silently chopped into different identities.

Queries go to external search engines; pages come from external sites; selected
output goes to the configured model. Local execution is not anonymous/on-device
inference. Extracted page text stays untrusted data, never agent instructions.

## Verification

Commands:

```sh
harness/.venv/bin/python -m unittest discover -s harness/tests -q
harness/.venv/bin/python harness/tests/browser_check.py
harness/.venv/bin/python harness/tests/chat_browser_check.py
# Opt-in stock-provider test, disposable session on isolated port 7802 only:
harness/.venv/bin/python harness/tests/live_chat_check.py
```

- **163 tests passed**, including existing native CAD/kernel, input, project,
  provenance, network-boundary and cancellation tests.
- Existing desktop/mobile/live-CAD browser check passed, no browser errors.
- Controlled streaming UI fixture passed: eight sources, two visits, multiple
  searches, failures/cancellation, long thinking, duplicate events, preserved DOM
  identities/selection/scroll, real page reload, source previews, unknown and
  snippet-only citations, keyboard controls, disabled web setting, missing
  favicon, mobile Canvas/Chat switching and reduced motion.
- Visual evidence (explicitly controlled fixture, not live source claims):
  `runs/harness-checks/chat-ui-asgflmtn/` (full viewport and cropped panel images).
- Real stock-model + WebSocket + close/reopen smoke:
  `runs/harness-checks/chat-live-a5mbwc9o/`. **103 genuine answer fragments**, no CAD
  operations, answer/source-independent history and Web-off preference preserved
  after reopening the project. All owned live test sessions were closed.
- All **18 main-project artifact hashes** still match their manifests.

### Live service limitations, separately observed

At 08:43 UTC, both DuckDuckGo attempts returned HTTP 403. Bing returned one Noctua
catalog result, not a verified exact-model specification. Navigation to that
result failed. At 08:44 UTC a separate Raspberry Pi official documentation read
succeeded: real page title, 80 headings, 10,940 characters of selected text and
explicit truncation of the 187,405-character extraction. Publication date was
unknown, so none was supplied. These probes do not establish mechanical fit or
universal search availability. Search ranking, upstream challenges and variant
selection remain limitations, not UI successes to disguise.

## Rollout

The isolated `cadpilot-harness-native-check` service on **7802** was restarted with
this backend and verified. Main **7800** has not been restarted for this change;
restart approval was requested. Shared static frontend files are available on
both instances, with compatibility for the legacy event stream. `/api/status`
reports `chat.version=1` when the new backend is loaded.

No native-tool feature flag was promoted, no user CAD documents were changed, no
dataset pipeline was modified, and no training was started. The completed v3
data rebuild and both training holds remain unchanged.
