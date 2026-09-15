# Local web research and CAD recovery — September 9, 2026

## Deployment

Deployed to `cadpilot-harness` / **7800 at 17:59:04 UTC**, following explicit user approval
to restart after testing. Main status reports supervision v4, research `local-chromium`, no
API key requirement, healthy planner/policy, HTTP 200 and no remaining old CAD sessions.
User project `d913b89ac1f946a0` remains at `r0001`; all six artifact SHA256s match the
pre-restart values. Saved files were not rewritten. Refresh CADPilot to load the new frontend.
Test server remains on 7802 with a separate project root; disposable test CAD sessions were closed.

## Scope and implementation

The user approved fixing the A100-chat harness failures and adding general web lookup, then
explicitly rejected paid/API-key search providers in favor of the supplied local-browser
reference implementation. Production code uses Python Playwright/Chromium with the reference's
DuckDuckGo → Bing → Brave selectors/fallback, not search-service APIs. Reference JS/HTML files
are untouched. The browser runs on the harness host, not directly inside the CADPilot tab;
no public CORS relays, Jina service, API keys or new public-facing proxy endpoint are required.

The planner has a `research` decision with query/URL and desired information. Returned sources
are retained with URLs, source IDs, retrieval times, bounded text and document links. A separate
structured extraction separates sourced facts from assumptions/unknowns. Fact quotes must occur
in a retrieved page/PDF (not a search snippet); arbitrary/fabricated source IDs are rejected.
These checks establish provenance, not semantic correctness or real-world fit. Sources are
untrusted user-message data, never injected into the system instruction. Native-model generation
receives the same research context. UI citations resolve only known source IDs.

The model panel exposes sources/assumptions and a research download; `research.json` is hashed
and stored with append-only revisions, including restore. Older revisions without this optional
file still open. The research call budget, recent-failure suppression, short-lived cache,
stop/steering cancellation, model-context limits and exact error feedback prevent silent loops.

Local Chromium uses a fresh context, native TLS and a private authenticated public-only CONNECT
proxy with DNS pinning. No personal cookies/profiles, CAD-session access, private-network URLs,
nonstandard ports, frames, service workers, WebSockets, automatic downloads, or non-GET page
requests. Navigation, network bytes and connection count are bounded. PDFs use a separate
networkless parser with memory/CPU/file bounds, no OCR, first 40 pages only. This is not a
production multi-user hosted browsing service; the application is still single-user/loopback.

## Recovery and presentation fixes

- Native errors now include the base/cutter bounds, volume, solid count, minimum separation
  and intersection volume. A cutter inside an existing void needs supporting material or a
  changed placement, not another identical call. Every required hole still must remove material.
- Failed raw recipes and errors are retained in a bounded 20-record `project/attempts/` directory,
  separate from committed models. Failed staging is still removed and the prior head preserved.
- The retry guard compares resolved geometry graphs, so renaming parameters/features/parts
  cannot count as a correction. An identical failed geometry is not executed again.
- Questions are emitted once via the pause event. The UI also deduplicates adjacent identical
  assistant/pause messages for old event replay. Terminal native errors are labeled failed.
- Native attempts and saved-base revision replace the misleading zero-of-80 display during
  native builds. Research calls do not count as CAD operations. Prior revisions remain visible.
- Full compiler errors are in expandable diagnostic cards. Web results have safe external links,
  snippets and notes, with no arbitrary HTML injection.

## Evidence and known limits

Regression suite includes real kernel error diagnostics, renamed-recipe repeat prevention,
quote/source validation, untrusted-context separation, DNS/private-address checks, authenticated
proxy controls, error feedback, steering cancellation, revision integrity/restore and UI replay.
Browser checks cover desktop/mobile, real CAD input, safe citations, duplicate questions and CSP.

Final verification: **101 tests passed**. `browser_check.py` passed with zero browser errors.
The final stock-model trial in `runs/harness-checks/web-research-3cjcq1hy` read the manufacturer
page, retained a quoted 124.5 mm mounting pitch, generated an editable 150×150×3 mm fan plate
with four Ø5 mounting holes and a Ø120 center cutout, and matched an independent FreeCAD
reference exactly: **0.0 mm³ symmetric difference**. Source evidence survived browser reload;
desktop/mobile checks passed with CSP enabled. Expected pitch was NOT supplied to the model.
Live search was probed separately with an Arduino query, demonstrating general rather than
A100-specific routing; this does not establish search quality for every product/query.

An initial live reader → stock-model → CAD trial passed independent fan-plate geometry grading
with zero symmetric difference (`runs/harness-checks/web-research-823a04tw`). A later trial
exposed an intermittent consent-dialog bug: removing all `aria-hidden` elements erased the
underlying product page. The extractor now removes dialogs instead, selects substantive main
content, and has an offline real-Chromium regression fixture for that exact DOM condition.
The failed trial is retained in `web-research-h5gfnao7`; it is not counted as a pass.

Live search can return challenge pages or off-topic responses; fallback is best-effort and
does not solve CAPTCHAs. Successful live probes used Bing after DuckDuckGo failed. No claim of
"forever unlimited" availability is made. General CAD competence, exact A100 GPU screw fit,
CFD/airflow optimization, assemblies/lofts, and trained-policy transfer are not established.

## Data-only recovery

The rebuild had stopped at 181/597 on a corrupt cached source video:
`autocad/d1191ab7-a72c-4f46-a089-142f74de2b7c/clip.mp4` was 492,865,837 bytes;
the pinned source requires 995,247,638 bytes and SHA256
`0360befde543776d90b70155616634fb7079b0335c13b8a80070a61a9b46a21d`.

`scripts/repair_v3_cache.py` downloaded and verified the replacement before moving the damaged
copy to `runs/v3-data-only-20260908/cache-repair-h4quhj7w/`. The old copy is recoverable.
Completed shards, frozen builder source, splits and sample-gate evidence were not changed.
The data-only gate check passed and `cadpilot-v3-data-only` resumed. It advanced to 184/597
at 17:59 UTC; final audits/assembly have not completed. Both training holds remain active:
no SFT, RL, training launch, inference shutdown or training UI was introduced.

The repair helper addresses a named corrupt cache file outside the immutable builder
fingerprint. The frozen downloader itself has not been modified; future corrupted downloads
can still stop the chain and must be repaired without bypassing its validation gate.
