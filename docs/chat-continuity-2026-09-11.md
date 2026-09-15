# CADPilot: conversation continuity, live model activity, dark mode

September 11 follow-up to the conversation-panel implementation.

## Root cause and changes

The paused Pi 3B conversation on test port 7802 confirmed a real context-loss
bug. Both the planner and native-operation loop received a list of user strings,
but not the assistant questions. Replies such as `a` and `yes do that` lost their
referents. Resuming also cleared the pause reason, and each native invocation
started fresh local feedback. UI persistence did not imply model-memory replay.

`server/dialogue.py` now projects actual user messages, completed public answers,
and clarification questions into bounded role-aware memory. Replies link to the
preceding question. Every planner/native/reviewer/research-note/desktop-policy
context receives that dialogue. Existing projects migrate by read-only replay
of their SQLite transcript; the projection is also saved in conversation.json.
New tasks reset it. Web content and provider reasoning never become dialogue or
authority. No inferred consent or invented decision summary is persisted.

Shared autonomy instructions distinguish already accepted provisional dimensions,
delegated design choices, genuine new blockers, and verification. Accepted guesses
remain guesses. A bounded reconsideration checks actual answered questions before
the model can pause again; it does not automatically authorize geometry or suppress
a genuinely new question. Native completion review now explicitly treats approved
provisional dimensions as limitations, not missing approval or proof of fit.

## Streaming contract

The existing schema-constrained CAD contract is retained; no migration to hosted
provider function tools is implied. The planner and native model use provider SSE.
Native operation JSON arrives incrementally for inspection while remaining
non-executable until the entire operation validates. Unexpected, unadvertised
provider function calls are rejected rather than executed.

- `thinking_start/delta/done`: actual model request identity, timestamps,
  offset-based provider `reasoning_content`/`reasoning` display text and terminal
  status. No synthetic reasoning, `<think>` parsing, or empty-reasoning placeholder.
- `tool_input_start/delta/done`: stable proposal identity, streamed bounded raw
  input in optional details. Input completion means **validating input**, not a
  completed tool. The native intent, result and errors retain that identity.
- Research requested by the native operation loop transitions the same draft
  entry into the real search/read entry with its existing source/result contract.
- `tool_settled`: explicit failure, cancellation, or completed non-geometry
  operation. Executable CAD only becomes saved/completed after the existing
  native validation and commit. Error feedback still reaches the next model call.
- Public answers still stream separately and append without remounting text.
  Questions appear only after the clarification check, so reconsidered draft
  questions do not become another apparent request for permission.

Reviews and full-recipe generation also stream provider-supplied reasoning/status;
incremental raw CAD-operation input is specific to the typed-operation path.
The desktop action policy retains its short, non-reasoning constrained response.
The UI does not claim that token streaming improves CAD accuracy or guarantees
completion. Failed/interrupted drafts are retained and cannot become successful
through late events. Replay, selection, cancellation and source history remain.

The configured local Qwen template was inspected: enabling thinking without an
effort setting defaults to **xhigh**. A live probe exhausted its response budget
before producing a plan. `planner.reasoning_effort: low` and the locally supported
`thinking_token_budget: 1024` now bound that stage, alongside an overall request
deadline. These are explicit endpoint capabilities; remove/adapt them when using
an incompatible provider. Display text is capped at 32,768 characters with visible
truncation metadata, and raw model content is bounded at 131,072 characters.

## Appearance

Dark is the default again. Explicit light/dark choices remain; the old automatically
persisted `system` value migrates to dark once. Future explicit System choices
persist normally. Theme and motion selectors remain in Tools & appearance.
Active provider reasoning can open promptly; a deliberate close is respected as
later text arrives. Missing reasoning produces no misleading empty disclosure.

## Verification

- **177 harness tests pass**, including native geometry/kernel regressions, old
  transcript migration, short-answer binding, bounded re-questioning, genuinely
  new blockers, actual HTTP fragment/cancellation handling, thinking budgets,
  and draft-input → validation → saved checkpoint identity.
- Real Chromium controlled streaming fixture: 76 events, zero browser errors,
  dark/light persistence, reasoning append/explicit-close behavior, progressive
  CAD input, stable DOM identity into execution, cancellation/late events, reload,
  source panels/citations, scroll/selection, narrow layouts and reduced motion.
  Evidence: `runs/harness-checks/chat-ui-6npgx6pn/`.
- Live stock-model HTTP + WebSocket + project reopen on isolated **7803**:
  `runs/harness-checks/chat-live-_uw0sf7g/`. 64 real reasoning fragments and
  157 real answer fragments; 8.23 seconds including reopen; zero CAD operations.
  Web-disabled preference and the complete event history survived reopening.
- Read-only stock-model continuation using the user's real saved questions,
  replies and research, but a disposable empty workspace:
  `runs/harness-checks/dialogue-live-4fmr94fe/`. Planner chose `model`, explicitly
  described the ports as provisional, and the native model chose `create_body`
  rather than asking again. The first proposal had unused parameters; the real
  compiler diagnostic produced a corrected, compiling proposal on the next try.
  2,411 reasoning fragments across requests, 291 input fragments, 88.93 seconds.
  **No CAD or web tool was executed by this probe.** It tests conversation
  continuation and first-operation correction, not an entire case or mechanical fit.
- All **32 artifact hashes** across main projects and the user's current test
  project match their saved manifests (18 main artifacts plus 14 in the test
  project). No saved user geometry was changed.

Earlier failed probes are retained, not counted as passes: an empty-research
fixture appropriately proposed a lookup; unbounded xhigh thinking exhausted
the plan budget; the first one-shot CAD probe failed on unused parameters.

Commands:

```sh
harness/.venv/bin/python -m unittest discover -s harness/tests -q
harness/.venv/bin/python harness/tests/chat_browser_check.py --base http://127.0.0.1:7803
harness/.venv/bin/python harness/tests/live_chat_check.py --base http://127.0.0.1:7803
harness/.venv/bin/python harness/tests/live_dialogue_check.py --source-project runs/harness-checks/native-projects/b388366e12bf44cb
```

## Rollout and limits

`/api/status` reports `chat.version=2` with `model_memory=role-aware-dialogue` when
this backend is loaded. The additive event contract remains compatible with
chat_protocol=1 snapshots. The user's paused 7802 session `abde0b89` was initially
left untouched and restart permission requested because its CAD window was open.
Before rollout, the app independently reported **zero open sessions** (the old
session endpoint returned 404). A second zero-session check immediately preceded
restarting **cadpilot-harness-native-check / 7802**. No CAD window was closed by
that restart. Status now reports chat.version=2, role-aware memory, and the bounded
reasoning configuration. Saved project `b388366e12bf44cb` remains available.
Refresh the frontend and reopen a saved project normally. **Main 7800 was not
restarted**, and native operations were not promoted there. The owned disposable
sessions and temporary verification backend on 7803 were closed.

No new native-operation flag was enabled on main. This pass does not establish
end-to-end Pi-case quality, exact connector placement, support for every CAD edit,
or universal elimination of model clarification errors. Old project transcripts
which never recorded assistant questions cannot reconstruct those missing words.
There were no training/data changes; both training holds remain in effect.
