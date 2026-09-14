# CADPilot in Docker

From the repository root:

```bash
harness/docker.sh up       # initialize private credentials, build, start, wait for health
harness/docker.sh url      # connection URL and location of initial credentials
harness/docker.sh connect  # shell in the running CAD container
harness/docker.sh check    # source workspace, CAD/compiler/browser/PDF/search smoke check
```

Open **http://127.0.0.1:7801**, sign in as **admin**, and read your generated password
from `harness/.docker/owner-password`. Each installation generates its own password;
there is no shared default. Change it with `harness/docker.sh reset-password`.
Password resets revoke all login sessions; the initial password file is then obsolete.
Sign out is available in the app's Settings dialog.
This standalone account controls one private workspace. For public signup and
independent user computers, use the public app described below.

The stack runs FreeCAD, OpenSCAD, virtual displays and the harness in `cad`, plus
SearXNG in a separate `searxng` service. The application is published only on the host
loopback address. SearXNG has no published port. Existing native harness services on
7800/7802 remain separate, so bringing up Docker does not close their CAD sessions.

Docker Engine with Compose and a Linux x86-64 runtime are required (Docker Desktop's
Linux VM can also provide that runtime). The image downloads FreeCAD 1.1.3 with a
SHA-256 check, installs OpenSCAD, and installs the locked Python/browser dependencies.
SearXNG is pinned by image digest. The first build downloads several GB. No model
weights, training data, local CAD projects or credentials enter the build context.

## Configuration and models

Run `harness/docker.sh init`, then edit `harness/.docker/compose.env` before starting:

```dotenv
CADPILOT_PORT=7801
CADPILOT_OWNER=admin
CADPILOT_MODEL_BASE_URL=http://host.docker.internal:8000/v1
CADPILOT_MODEL=qwen3.8-27b
CADPILOT_NATIVE_OPERATIONS=1
```

Inference runs on the existing host/model server. It must listen on an interface
reachable from Docker; `127.0.0.1` inside the container refers to the container.
The default uses Docker's host gateway. Providers and API keys can subsequently be
changed in the app's Settings. Persisted settings take precedence for the assistant;
the environment seeds the initial provider and configures the visual policy.
Native operations match the current experimental workspace; they do not certify
that a model satisfies every requested design requirement. Set the native switch
to `0` to use the legacy hybrid workflow.

Model generation has **no default time limit**, including while loading a model or
waiting for its first token. Stop and steering still cancel pending inference.
In `harness/config.yaml`, `request_timeout_s`, `native_request_timeout_s`, and
`planner_request_timeout_s` accept `0`/`null` for unlimited or a positive duration
in seconds. There is no five-minute ceiling. Connection failures and malformed JSON
are still reported. Rebuild/restart to apply configuration-file changes.

SearXNG uses `/search?format=json` for web and image search. Edit the generated
`harness/.docker/searxng/settings.yml` to choose engines. Sources still pass the
harness's public-URL checks; opened HTML uses sandboxed Chromium, and PDF parsing
runs in a networkless worker. SearXNG replaces browser-based *search*; the small
public-only proxy is retained for safe page reading. Engine throttling can still occur.
See the upstream [JSON search API](https://docs.searxng.org/dev/search_api.html)
and [container configuration](https://docs.searxng.org/admin/installation-docker.html).

## Data and lifecycle

Projects/exports, owner credentials/browser sessions/settings, learned facts and
search cache live in separate named Docker volumes. `up`, rebuilds, `stop` and
`down` preserve those volumes. `down -v` deletes them. Live GUI processes and
unsaved manual edits do not survive a container restart; save before rebuilding.
Existing host projects are not automatically moved into Docker.

```bash
harness/docker.sh build    # build without starting/restarting services
harness/docker.sh status
harness/docker.sh logs cad
harness/docker.sh stop
harness/docker.sh down
```

`connect` opens a shell as the non-root application user. No host project/home
directories, Docker socket or GPU are mounted. The configuration allows nested
unprivileged namespaces with `seccomp`, `apparmor` and `systempaths` exceptions so
bubblewrap and Chromium can run their own sandboxes. All Linux capabilities are
dropped, privilege escalation is disabled, and startup probes the sandbox. Hosts
that forbid user namespaces cannot run this configuration. Docker documents the
[system path option](https://docs.docker.com/reference/cli/docker/container/run/).

Authentication protects APIs, artifacts and both WebSockets. Passwords use salted
PBKDF2-SHA256; the database stores hashes of random session tokens. Cookies are
HttpOnly, SameSite=Strict, expire after 12 hours, and use Secure on HTTPS. Login
attempts are rate limited, and logout/password resets revoke tokens. The shipped
deployment is a local service; public HTTPS/domain/reverse-proxy configuration is
a separate deployment choice.

The optional browser/inference check is:

```bash
harness/.venv/bin/python harness/tests/docker_browser_check.py
harness/.venv/bin/python harness/tests/docker_browser_check.py --live-model
```

The second command creates a disposable CAD session, asks the model to build and edit
a box, measures both saved volumes and downloads STEP exports. It closes its session
and retains test artifacts under `runs/harness-checks/`. Its wait budget is a test
setting (`--model-timeout`, `0` for unlimited), independent of application timeouts.

## Public app and per-user CAD computers

The public app is a separate service. Start/update it with:

```bash
harness/docker.sh portal-up
```

It listens on **0.0.0.0:7803**, with the configured public origin
**https://cad.armand0e.com**. Point the Cloudflare Tunnel's public hostname at
`http://127.0.0.1:7803` (cloudflared running on this host). Keep the public Host
header. A locally managed tunnel can use this ingress entry:

```yaml
ingress:
  - hostname: cad.armand0e.com
    service: http://127.0.0.1:7803
  - service: http_status:404
```

New users create an account, generate a connection command, run it in Bash, configure a
vision/tool-capable model, and enter the studio. CAD APIs are gated until pairing
and model validation finish. Each account routes to one independently owned CAD
runtime. Projects, provider settings/keys, CAD processes and SearXNG live on that
user's computer. Uploads, downloads and viewport/control WebSockets travel over
an outbound connection to the public app; users need no inbound port or tunnel.

The standalone owner workspace on 7801 keeps its existing login and volumes.
Its projects are not shared with public accounts. The portal has its own account
volume and session cookie; changing one does not overwrite the other.

### Install from GitHub

Source and Docker build files are published at **https://github.com/armand0e/CAD-Pilot**.
Onboarding has Windows, Linux and macOS tabs and generates a private, copyable command
for the signed-in account. Switching tabs reuses the same pairing code. It downloads
`install.sh` from GitHub, clones the repository into `~/CAD-Pilot`, then runs
`harness/connect.sh` to build `compose.worker.yaml` and start CAD plus SearXNG.
Install Git, curl, Bash and Docker with Compose first. The Windows tab uses PowerShell
to download the installer and pass it to Bash in the default WSL distribution. Enable
[Docker Desktop WSL integration](https://docs.docker.com/desktop/features/wsl/)
for that distribution and install Git/curl inside it. Linux uses a Bash command;
macOS runs it in Terminal with Docker Desktop's Linux amd64 support.
The CAD image uses Linux x86-64. It does not install or download model weights.

The installer prints a workspace-specific `cadpilot` command for logs, stopping,
restarting and opening a container shell. It stores private connection files under
`harness/.docker/workers/<instance-id>/`, excluded from Git. An existing clean `main`
checkout is updated with `git pull --ff-only`. Saved projects and provider credentials
stay in named Docker volumes. Rerunning the command updates the same installation.
Set `CADPILOT_INSTALL_DIR` before running to choose a different source directory.

A new pairing command revokes the old worker credential. On the same computer, rerun
it using the same source directory to retain local settings. The installer keeps the
same Compose project and volume names as the ZIP setup. Move a previous ZIP installation's
search settings into the new worker directory if you customized its engines. To move to
another computer, back up and transfer your Docker volumes separately.

The ZIP download remains available under **Reconnect or replace a computer**. It builds
the bundled source by default. `CADPILOT_RUNTIME_IMAGE` optionally selects an already
published image for ZIP downloads; the GitHub command always builds the checked-out source.
Do not use `docker compose down -v` unless intentionally deleting saved projects.

Pairing codes expire after 24 hours to allow slow first builds. The container exchanges its code for a
revocable credential stored with mode 0600 in its state volume, then acknowledges
receipt. Generating a fresh command or ZIP revokes the previous credential. Heartbeats
and reconnects handle tunnel restarts. A disconnected computer returns users to
onboarding instead of connecting them to somebody else's workspace. The portal
must run as one process/replica; its live connection registry is in memory.

HTTPS cookies are determined from the explicit public origin, so TLS termination
at Cloudflare does not turn off Secure cookies. The public listener uses the
network peer for signup/login rate limits. Enable `CADPILOT_TRUST_CLOUDFLARE`
only when direct access is restricted to a trusted proxy, so clients cannot
forge `CF-Connecting-IP`. See
[Cloudflare's header documentation](https://developers.cloudflare.com/fundamentals/reference/http-headers/)
and [WebSocket behavior](https://developers.cloudflare.com/network/websockets/).

## Pi runtime and CAD tools

The runtime includes **`@earendil-works/pi-coding-agent` 0.85.1** and Node 24.
It uses Pi's [AgentSession SDK](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/sdk.md)
directly. `harness/pi/runtime.mjs` registers CADPilot's typed tools, and
`harness/server/pi_agent.py` bridges tool execution and browser events.

Pi owns model requests, the agent loop, conversation, steering, retries and compaction.
CADPilot keeps the CAD kernel, geometry checks, revision commits, research tools and UI.
There is no custom native context compactor or secondary native agent loop.

- Pi saves conversation trees in each project's `pi/sessions/*.jsonl`, inside the existing
  projects volume. Existing role/tool histories are imported once. Older GUI chats migrate
  from their full chat transcript. Stop/reopen continues the saved Pi session.
- Submitted messages remain in a durable inbox until Pi has written them. This protects
  messages and image attachments if the process exits during its first model request.
- Reference images belong to their user messages; image tools return actual image pixels.
  `view_image` rereads or crops full-resolution originals, including after compaction.
  Rendered CAD views are returned with geometry tool results. `inspect` reads the current
  workspace, brief, research, selection and reference IDs.
- Pi handles steering at its next safe turn boundary. Stop aborts inference and pending
  tool work. Pause holds CAD execution at a tool boundary. Incomplete tool calls are
  handled by Pi; validation and failed-geometry guards remain in the CAD executor.
- Pi uses the detected/configured context window for compaction. Set **Context window**
  in model settings if `/models` does not report one; unknown models use a conservative
  32,768-token fallback. No model request deadline is enabled by default.
- The project `last-model-request.json` records Pi's outgoing payload, with image pixels
  replaced by MIME, size and hash metadata. It contains no provider API key.
- If vLLM rejects Pi's estimated input/output allocation, the transport retries that
  rejected request once with vLLM calculating the output allowance from its actual
  tokenizer and server settings. Subsequent uncapped requests on that connection use
  this server allowance. Input, images, tools and thinking settings are preserved;
  explicit output limits (including Pi's summary budget) remain in effect. True input
  overflow still goes to Pi's compaction. The request diagnostic records the actual
  transmitted payload, including allocation retries.
- CAD overviews are limited to 24,000 serialized characters. Full specifications
  stay in `design-spec.json`; oversized overview records are available to Pi read
  in `.cadpilot-context/`, excluded from source builds and dirty-file checks.
  `inspect` sends saved research notes and source links instead of repeating pages.
- After a source build, Pi's active tools omit incompatible legacy geometry and
  parameter operations immediately. Existing operation-based projects retain them.
- Pi research tool results include the returned URLs, source IDs, page text and
  document links. Pi reads this evidence directly; the legacy per-page extraction
  model call is skipped. Child researchers return cited findings and explicit gaps
  once the requested dimensions are covered or available documentation is exhausted.
- `spec_update` patches rows by ID. Removing a requirement requires retaining a
  retired row with its reason and user evidence. Verification records distinguish
  numeric CAD comparisons, visual observations, and file existence/hash checks;
  old unscoped claims are preserved but displayed as needing verification.
- User-input evidence uses an indexed, incremental transcript reader. Older
  transcript databases migrate in place while preserving every UI event.
- Each research call has its own card in the chat showing actual activity, elapsed
  time and search/read counts. It opens on an activity trail and the dimensions
  being investigated, with Sources and Findings tabs for links, outcomes and
  unresolved questions. Cards stay at their invocation through steering and
  reconnects; repeated progress preserves the selected tab and disclosure state.
  These compact UI events survive reconnects;
  child reasoning and complete source pages remain in the separate investigation.

`harness/pi/setup.sh` installs the pinned packages for native development. Docker uses
`npm ci` and includes the same lockfile. Downloaded onboarding bundles include the Pi
sources and dependency lockfile, so paired containers build the same runtime.

Checks: the harness unittest suite runs the real Pi SDK against a deterministic HTTP model;
`portal_browser_check.py` exercises real Docker pairing, model turns and reference upload.
After building a worker image, run `python3 harness/tests/docker_workspace_upgrade_check.py cadpilot:paired`
to exercise an upgrade with a private temporary knowledge volume. It checks missing and stale
guides, recovery of an unfinished workspace, Pi requests/tools/session resume, a real source
build, and preservation of saved notes and facts. Required workspace guides ship in
`harness/server/guides/`; the `knowledge` volume holds editable notes and learned facts.

### Connection diagnostics

A paired worker is healthy only while connected to its portal. `/healthz` returns
503 with a reason while resolving DNS, pairing or reconnecting; a portal or standalone
server still checks its own service health. The installer shows recent logs on failure.
Run the printed workspace command followed by `exec -T cad python -m server.connector`
to inspect the connection without exposing credentials. For ZIP setups, use
`docker compose exec -T cad python -m server.connector`.

Worker Compose configurations use Cloudflare DNS (1.1.1.1 and 1.0.0.1) to avoid stale
host DNS records that return only IPv6 on an IPv4 Docker network. Set
`CADPILOT_DNS_PRIMARY` and `CADPILOT_DNS_SECONDARY` in the worker env file (or the ZIP
folder's `.env`) if your network requires other resolvers. Docker service names and
`host.docker.internal` remain available. A `pairing_rejected` response needs a fresh
onboarding command; `proxy_rejected` means the tunnel's Cloudflare rules must permit
non-browser WebSocket connections to `/api/worker/connect`.
