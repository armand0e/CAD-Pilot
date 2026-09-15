# CADPilot workspace update — September 7, 2026

The workspace now has a responsive launcher, resizable assistant panel, task suggestions,
collapsible action cards, run progress and timing, pause/resume, take-control, activity export,
and keyboard help. Development targets are hidden by default.

September 8 correction: removed the dataset/training status UI, its polling, and the server
endpoint. The app pairs with the independently released model; training monitoring stays
outside the product.
Normal CAD windows fill the display automatically while dialogs preserve their geometry.

Runner state and bounded event history are replayed on every WebSocket connection. Reloads
and session switches recover the active mode, control lock, pause state, and activity. Stop
cancels pending HTTP requests and action delays; already executing input operations finish
before the screen unlocks. Manual-to-Auto changes wake the runner, Auto consumes queued
objectives, and new tasks discard old queued objectives. Transient transport/server failures
retry twice. Blank startup screens wait for application rendering; empty model output errors;
repeated unchanged-screen actions pause for review; application exit stops the runner.

Validation:

- 10 deterministic runner tests passed, covering multiple Auto steps, Manual-to-Auto wakeup,
  pause/resume, Stop during HTTP and before input injection, stale queue cleanup, event replay,
  empty responses, subscriber overflow, blank startup, and loop detection.
- All 33 existing data pipeline tests passed. Shell syntax and Python compilation passed.
- Chromium checks at 1440×900 and 390×844 passed with no JavaScript errors, including a real
  OpenSCAD display, browser reload, session restoration, and the session-close dialog.
- A live policy check completed two guided OpenSCAD steps across a browser reload, paused,
  resumed control within a two-second Stop check, and exported server activity. No browser
  errors. The final screenshot confirms the CAD application fills the display.

Screenshots: `runs/harness-checks/workspace-desktop.png`, `workspace-mobile.png`, and
`workspace-agent.png`. These checks validate interaction/lifecycle behavior, not end-to-end
CAD geometry or task-completion accuracy. History is retained for the server process lifetime.

The main server is the `cadpilot-harness` user service at http://127.0.0.1:7800. Test sessions
were closed and the temporary test service stopped. The general Qwen endpoint remains the
policy until the trained adapter is served.

The full 597-workflow chain continues independently as `cadpilot-full-run`, with automatic
restart, checkpoint resume, complete-corpus audit, stage logs, and success markers. At the
final check, 533 shards were complete and about 417 GiB remained free. Full training and
evaluation have not completed yet; only `runs/full-run/complete.ok` signals verified success.
