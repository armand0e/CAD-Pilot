# CADPilot

A browser CAD workspace with FreeCAD, OpenSCAD, web research through SearXNG, and
an agent built on the upstream [Pi SDK](https://github.com/earendil-works/pi).
Pi manages the agent loop, conversation, steering and compaction. CADPilot supplies
CAD tools, geometry checks, project revisions, reference images and the live studio.

## Connect your computer

1. Open **https://cad.armand0e.com** and create an account.
2. Install **Docker with Compose, Git, curl and Bash** on your CAD computer.
3. In onboarding, choose **Windows**, **Linux** or **macOS**, then select
   **Generate connection command**. Copy and run it in the terminal shown by your tab.
4. Wait for Docker to build FreeCAD, OpenSCAD and Pi and start SearXNG. Return to
   onboarding, configure an image/tool-capable model, and enter the studio.

The command downloads this repository and builds the image locally. It pairs the
container with your account over an outbound connection; your computer needs no
public port or extra tunnel. Keep Docker running while using the studio.
Linux x86-64 is the native target. Docker Desktop can provide an amd64 Linux runtime
on macOS. The Windows tab gives a PowerShell command that runs the installer through
your default WSL distribution with Docker Desktop integration.

The first build downloads several GB. A one-use pairing code lasts 24 hours.
Saved projects, settings and the permanent connection credential persist in Docker
volumes. Rerun a fresh onboarding command to update/reconnect the same installation.
Connection commands are private; do not share them. Model weights are not included.
For a model served on the Docker host, use `http://host.docker.internal:8000/v1`.

The reasoning menu includes **Off**. Qwen3.8 uses **Low**, **Medium**, and **Xhigh**;
these are sent in `chat_template_kwargs.reasoning_effort`, while Off sends
`enable_thinking: false`. An empty thinking budget adds no explicit token budget.
Pi fits output into the remaining context; CADPilot does not impose an 8k output cap.
Leave the context window empty to detect changes when your model server restarts.
Runtime fixes require updating the paired container, as well as the public portal.

**Images per request** is configurable for each provider in Settings (default 16,
matching the local vLLM endpoint). CADPilot uses Pi's context hook to send current
CAD views and prioritize uploaded references and explicitly requested images.
Superseded CAD pixels and duplicate images leave the outgoing request; original
images, revision records, tool results and Pi's saved conversation remain intact.
`view_image` can reopen saved CAD views by IDs such as `cad:r0001:top`, including
after compaction. Oversized reference collections show a notice and remain
available for the assistant to inspect in batches. Pi still owns compaction.

## Run your own portal

```bash
git clone https://github.com/armand0e/CAD-Pilot.git
cd CAD-Pilot
CADPILOT_PUBLIC_ORIGIN=https://cad.example.com harness/docker.sh portal-up
```

The portal listens on **0.0.0.0:7803**. Point your HTTPS reverse proxy or Cloudflare
Tunnel at `http://127.0.0.1:7803`, preserving the public Host header. The portal runs
as one process/replica and stores accounts in a named Docker volume. Each account
connects to its own CAD computer. Account signup and pairing come before the studio.

## Standalone local workspace

```bash
harness/docker.sh up       # build CAD + SearXNG; http://127.0.0.1:7801
harness/docker.sh connect  # open a shell in the CAD container
harness/docker.sh check    # CAD/compiler/browser/search checks
harness/docker.sh stop
```

Sign in as `admin` with the generated password in `harness/.docker/owner-password`.
This is a separate local workspace; it does not pair with a portal account.

## Development and verification

```bash
python3 -m venv harness/.venv
harness/.venv/bin/pip install -r harness/requirements-lock.txt
harness/pi/setup.sh
PYTHONPATH=harness harness/.venv/bin/python -m unittest discover -s harness/tests -p 'test_*.py'
```

Native GUI/kernel checks need the CAD applications and system tools described in
[the harness guide](harness/README.md); Docker contains those dependencies.
The Pi bridge uses pinned npm dependencies and saves session JSONL with each project.
Model requests have no generation deadline by default; Stop cancels inference.

See [Docker deployment and persistence](harness/docker/README.md) for lifecycle,
pairing, tunnel configuration, model connections and the optional browser checks.
