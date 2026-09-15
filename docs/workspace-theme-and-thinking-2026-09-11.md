# Workspace theme, following reasoning, and stalled JSON recovery

## Delivered UI

`harness/web/theme.css` is the shared palette/typography/motion layer for the
whole app. The header, launcher, app/project picker, canvas surround, Model
drawer, controls, dialogs, toasts and conversation now use the same warm neutral
light/dark tokens. A header appearance button synchronizes with Tools & appearance,
persists the choice, and retains System/reduced-motion support. Native FreeCAD and
OpenSCAD pixels and input mapping are deliberately unchanged.

`web/chat/thinking.js` owns append-only provider text and its local scroll state:

- The normal viewport is capped at 200 px; Show more expands to at most 420 px
  or half the screen height, still scrollable.
- Incoming text follows the bottom until the user scrolls upward with pointer,
  wheel, keyboard or touch. “Latest thinking” resumes following. Returning to the
  bottom also resumes it. A text selection prevents automatic scrolling.
- New spans fade from low opacity over 280 ms. Existing spans and selections
  remain intact; history replay does not animate. Reduced motion is static.
- A thin, theme-colored scrollbar is transparent until hover or keyboard focus.
  A stable gutter avoids rewrapping text when the scrollbar appears.
- Overflowing panels reserve the controls row so “Latest thinking” appearing does
  not shift the surrounding transcript, including short text with many newlines.
- Opening/closing reasoning remains user-controlled; an incoming fragment does
  not reopen a deliberately closed disclosure. Transcript scrolling is separate
  and does not follow a reasoning panel the user is reading farther up.

No provider reasoning is invented or inferred. The existing source timeline,
citations, Stop/steering, persisted conversation, exports and revision controls
remain connected to the real application.

## Model timeout investigation and fix

The user reported a 60-second timeout. A later observed spacer request in session
`c15b0274` hit the native 180-second bound while emitting 7,950 tool-input fragments
(11,742 characters), mostly spaces/newlines in malformed JSON. No native operation
from that response had executed. This demonstrates a runaway generation problem,
not simply a slow CAD kernel. It does not establish that every timeout has that cause.

`JsonStreamGuard` in `server/chat_stream.py` detects more than 256 consecutive JSON
formatting whitespace characters across fragments and literal control characters
inside strings. Escaped newlines, normal pretty-printing and whitespace inside
legitimate string values remain allowed. It is applied only to JSON responses,
not provider display reasoning. Failed streams close promptly and retain the exact
rejected prefix and stable operation identity for bounded correction feedback.
Neither partial proposals nor failed drafts execute. Planner retries remain at
three and native failures at four; cancellation is not retried.

The configured local vLLM supports `structured_outputs.whitespace_pattern`.
`planner.structured_output_whitespace_pattern: '[ \n\t]{0,8}'` now bounds JSON
padding during constrained decoding. This is an explicitly configured endpoint
capability, not a portable provider field. The request repeats the same JSON
constraint in the extension because vLLM validates it before merging
`response_format`. An initial smoke test caught/rejected an options-only extension
with HTTP 400; the corrected request was live-tested successfully.

Planner generation has a configurable 120-second overall deadline; native input
remains bounded at 180 seconds. These limits do not guarantee a correct model or
prevent every kind of generation loop. The existing output limits remain.

## Verification and evidence

- **183 harness tests pass**, including real native kernel tests, fragment-level
  stall/control-character detection, stream closure/no partial execution, planner
  recovery, exact native correction feedback, and vendor option gating.
- `tests/chat_browser_check.py`: **88 controlled events**, zero browser errors.
  Added local autoscroll, scroll-up preservation, resume, bounded expanded height,
  keyboard Home, preserved selection/DOM identity, scrollbar visibility and stable
  width, append animation, reduced motion, replay, themes and 320–1440 px layouts.
  Evidence: `runs/harness-checks/chat-ui-nlc9evk1/`.
- `tests/workspace_browser_check.py`: controlled launcher, project reopening,
  exports, parameters, tools selection, dialogs, theme persistence/System changes,
  mobile canvas/Model drawer and no pixel filtering. Zero browser errors; backend
  routes mocked, no user CAD/project changes.
  Evidence: `runs/harness-checks/workspace-theme-kzo2lak6/`.
- `tests/browser_check.py`: actual disposable OpenSCAD display, mouse/key input,
  reconnect, dialogs and supervision UI passed. Owned test session closed;
  the user's FreeCAD session was not touched.
- `tests/live_stream_check.py`: actual stock Qwen3.8-27B first spacer operation
  generated and compiled in memory in **15.68 seconds**, after one correctable
  unused-parameter error. 508 reasoning and 149 tool-input fragments, no whitespace
  stall, **zero CAD executions**. This is not a full spacer build or fit test.
  Evidence: `runs/harness-checks/spacer-stream-hsdgncub/`.

## Rollout

The static UI is available immediately on refresh. The backend stream guard and
new configuration require a restart. Restart approval was requested separately:
the user's FreeCAD session `c15b0274`, project `27535de649264e8d`, remains open on
7802, so the service was **not restarted** during this pass. Once loaded,
`/api/status` includes `chat.json_stream_guard: true` (chat protocol stays version 2).
Main 7800 was not restarted or promoted to native operations. Data/training and
existing CAD artifacts were not changed.
