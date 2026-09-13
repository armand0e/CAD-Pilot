"""CADPilot server: session API, live viewport stream, and the agent WebSocket."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from pathlib import Path

import yaml
from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .agent import AgentRunner
from .detect import detect_apps, missing_system_tools
from .sessions import SessionManager
from .protocol import decode_message, desktop_message
from .security import LocalWorkspaceSecurity
from .projects import Project
from .presentation import present_revision
from .edit_guard import note_input, acknowledge_saved
from . import settings as settings_store
from . import auth

HARNESS_ROOT = Path(__file__).resolve().parents[1]
CONFIG = yaml.safe_load((HARNESS_ROOT / "config.yaml").read_text(encoding="utf-8"))
for role in ('planner', 'policy'):
    if os.environ.get('CADPILOT_MODEL_BASE_URL'):
        CONFIG[role]['base_url'] = os.environ['CADPILOT_MODEL_BASE_URL'].rstrip('/')
    if os.environ.get('CADPILOT_MODEL'):
        CONFIG[role]['model'] = os.environ['CADPILOT_MODEL']
if os.environ.get('CADPILOT_SEARXNG_URL'):
    CONFIG.setdefault('research', {})['searxng_url'] = os.environ['CADPILOT_SEARXNG_URL']
SETTINGS = settings_store.load(CONFIG)
settings_store.apply(CONFIG, SETTINGS)
if 'CADPILOT_NATIVE_OPERATIONS' in os.environ:
    # Explicit operator-owned test/release switch, never a model or HTTP input.
    mode = os.environ['CADPILOT_NATIVE_OPERATIONS']
    if mode not in ('0', '1'):
        raise ValueError('CADPILOT_NATIVE_OPERATIONS must be 0 or 1')
    CONFIG['agent']['native_operations'] = mode == '1'

app = FastAPI(title="CADPilot")
auth.install(app, HARNESS_ROOT)
app.add_middleware(LocalWorkspaceSecurity)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"])
manager = SessionManager(width=int(CONFIG["session"]["width"]), height=int(CONFIG["session"]["height"]),
                         max_sessions=int(CONFIG["session"].get("max_sessions", 4)))
_apps_cache = {"ts": 0, "apps": []}
PROJECT_ROOT = Path(os.environ.get('CADPILOT_PROJECT_ROOT', HARNESS_ROOT / 'projects')).resolve()
_opening_projects = set()


async def _applications():
    import time
    if time.monotonic() - _apps_cache["ts"] > 30 or not _apps_cache["apps"]:
        _apps_cache["apps"] = await asyncio.to_thread(detect_apps)
        _apps_cache["ts"] = time.monotonic()
    return _apps_cache["apps"]


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(HARNESS_ROOT / "web" / "index.html")


app.mount("/static", StaticFiles(directory=HARNESS_ROOT / "web"), name="static")


_endpoint_cache: dict = {"ts": 0.0, "policy": None, "planner": None}


async def _endpoint_health() -> dict:
    import time as _time

    import httpx

    if _time.time() - _endpoint_cache["ts"] < 10:
        return _endpoint_cache
    async with httpx.AsyncClient(timeout=1.5) as client:
        for name in ("policy", "planner"):
            try:
                headers = {'Authorization': 'Bearer ' + CONFIG[name]['api_key']} if CONFIG[name].get('api_key') else {}
                response = await client.get(f"{CONFIG[name]['base_url'].rstrip('/')}/models", headers=headers)
                items = response.json().get("data", []) if response.status_code == 200 else []
                ids = {item.get("id") for item in items}
                _endpoint_cache[name] = CONFIG[name]["model"] in ids
                served = next((item for item in items if item.get("id") == CONFIG[name]["model"]), None)
                # Explicit user limits stay fixed; autodetected limits follow
                # the server when it is restarted with a different context size.
                explicit_window = (next((m.get('context_window') for m in SETTINGS['models']
                                        if m['name'] == SETTINGS['active_model']), None)
                                   if name == 'planner' else CONFIG[name].get('max_model_len'))
                if served and not explicit_window and type(served.get('max_model_len')) is int and served['max_model_len'] > 0:
                    changed = CONFIG[name].get('max_model_len') != served['max_model_len']
                    CONFIG[name]["max_model_len"] = served["max_model_len"]
                    if changed and name == 'planner':
                        for session in manager.sessions.values():
                            if session.agent:
                                session.agent._wake.set()
            except Exception:  # noqa: BLE001
                _endpoint_cache[name] = False
    _endpoint_cache["ts"] = _time.time()
    return _endpoint_cache


@app.get("/api/status")
async def status() -> dict:
    health = await _endpoint_health()
    return {
        "missing_tools": missing_system_tools(),
        "sessions": [session.to_json() for session in manager.sessions.values()],
        "policy": {k: v for k, v in CONFIG["policy"].items() if k != "api_key"} | {"ok": health["policy"]},
        "planner": {k: v for k, v in CONFIG["planner"].items() if k != "api_key"} | {"ok": health["planner"], "has_key": bool(CONFIG["planner"].get("api_key"))},
        "chat": {"version": 2, "streaming": "provider-reasoning-and-cad-input", "history": "project-sqlite",
                 "model_memory": "pi-session-jsonl" if CONFIG['agent'].get('native_operations') else "role-aware-dialogue",
                 "runtime": "pi-coding-agent@0.85.1" if CONFIG['agent'].get('native_operations') else "visual-policy",
                 "web_toggle": True, "json_stream_guard": not CONFIG['agent'].get('native_operations', False)},
        "research": {"enabled": bool(CONFIG.get('research', {}).get('enabled', False)),
                     "backend": "searxng" if CONFIG.get('research', {}).get('searxng_url') else "local-chromium", "api_key_required": False},
        "supervision": {"version": 5, "steering": "conversation", "visual_review": True, "repeat_guard": True,
                        "native_tools": True, "persistent_revisions": True,
                        "native_operations": bool(CONFIG['agent'].get('native_operations', False)),
                        "native_geometry_verified": False, "verification_scope": "per-artifact native solid validity, not task certification"},
    }


@app.get('/api/settings')
async def get_settings():
    return settings_store.public(SETTINGS)


@app.put('/api/settings')
async def put_settings(body: dict):
    global SETTINGS
    try:
        candidate = settings_store.validate(body, previous=SETTINGS)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    settings_store.save(candidate)
    SETTINGS = candidate
    settings_store.apply(CONFIG, SETTINGS)
    _endpoint_cache['ts'] = 0.0
    await _endpoint_health()
    for session in manager.sessions.values():
        runner = getattr(session, 'agent', None)
        if runner:
            runner._wake.set()
            runner.research_tool.config['enabled'] = SETTINGS['web_search']
            if not SETTINGS['web_search']:
                runner.set_web_enabled(False)
    return settings_store.public(SETTINGS)


@app.get("/api/apps")
async def apps() -> dict:
    return {"apps": [application.to_json() for application in await _applications()]}


@app.post("/api/sessions")
async def create_session(body: dict) -> dict:
    if not isinstance(body.get("app_id"), str):
        raise HTTPException(422, "app_id must name an installed CAD application")
    applications = {application.id: application for application in await _applications()}
    application = applications.get(body.get("app_id", ""))
    if application is None:
        raise HTTPException(404, f"unknown app {body.get('app_id')!r}")
    if not isinstance(body.get('engine', 'hybrid'), str) or body.get('engine', 'hybrid') not in {'hybrid', 'visual'}:
        raise HTTPException(422, 'engine must be hybrid or visual')
    project = None
    if body.get('project_id'):
        project = _project(body['project_id'])
        if project.read()['app_id'] != application.id:
            raise HTTPException(409, 'Reopen this project with its original application')
        if project.id in _opening_projects or any(s.project and s.project.id == project.id for s in manager.sessions.values()):
            raise HTTPException(409, 'This project is already open in another session')
    elif application.id in {'freecad', 'appimage-freecad', 'openscad', 'appimage-openscad'}:
        project = Project.create(PROJECT_ROOT, application.id)
    try:
        if project:
            _opening_projects.add(project.id)
        session = await asyncio.to_thread(manager.create, application.to_json() | {"accent": application.accent,
                                          'native_project': bool(project) and body.get('engine', 'hybrid') == 'hybrid'})
    except (RuntimeError, OSError) as error:
        raise HTTPException(500, str(error)) from error
    finally:
        if project:
            _opening_projects.discard(project.id)
    session.project = project
    session.engine = body.get('engine', 'hybrid')
    session.project_busy = True
    warning = None
    if project and project.read()['head']:
        try:
            await present_revision(session)
        except Exception as error:
            warning = str(error)
    session.project_busy = False
    return session.to_json() | {'warning': warning}


def _project(project_id):
    try:
        return Project(PROJECT_ROOT, project_id)
    except (ValueError, TypeError) as error:
        raise HTTPException(404, 'No such project') from error


@app.get('/api/projects')
async def projects():
    result = []
    for path in PROJECT_ROOT.iterdir() if PROJECT_ROOT.exists() else []:
        try:
            info = Project(PROJECT_ROOT, path.name).read()
            if info['head']:
                result.append({k: info[k] for k in ('id', 'name', 'app_id', 'head', 'created')})
        except (ValueError, OSError):
            continue
    return {'projects': sorted(result, key=lambda p: p['created'], reverse=True)[:100]}


@app.get('/api/projects/{project_id}')
async def project_info(project_id: str):
    try:
        return _project(project_id).public()
    except ValueError as error:
        raise HTTPException(409, str(error)) from error


@app.get('/api/projects/{project_id}/workspace')
async def source_workspace(project_id: str):
    from .source_workspace import SourceWorkspace
    try:
        work = SourceWorkspace(_project(project_id))
        return await asyncio.to_thread(work.describe)
    except (ValueError, OSError) as error:
        raise HTTPException(409, str(error)) from error


@app.get('/api/projects/{project_id}/investigations/{investigation_id}')
async def dimension_investigation(project_id: str, investigation_id: str):
    from .projects import Project
    try:
        project = Project(_project(project_id).path / 'research-tasks', investigation_id)
        # Explicitly requested detail stays out of the parent model's context.
        from .transcript import Transcript
        report = project.path / 'dimension-report.json'
        return {'investigation_id': investigation_id,
                'report': json.loads(report.read_text()) if report.is_file() else None,
                'events': Transcript(project.path / 'chat.sqlite3').read()}
    except (ValueError, OSError) as error:
        raise HTTPException(404, str(error)) from error


@app.get('/api/projects/{project_id}/workspace/file')
async def source_file(project_id: str, path: str):
    from .source_workspace import SourceWorkspace, digest, MAX_TEXT
    try:
        work = SourceWorkspace(_project(project_id))
        work.ensure()
        file = work.file(path)
        if not file.is_file() or file.stat().st_size > MAX_TEXT:
            raise ValueError('Text editor supports files up to 1 MiB')
        data = file.read_bytes()
        return {'path': path, 'content': data.decode('utf-8'), 'sha256': digest(data)}
    except (ValueError, OSError) as error:
        raise HTTPException(409, str(error)) from error


@app.put('/api/projects/{project_id}/workspace/file')
async def save_source_file(project_id: str, body: dict):
    from .source_workspace import SourceWorkspace
    if any(s.project and s.project.id == project_id and (getattr(s, 'project_busy', False) or s.agent and s.agent.active)
           for s in manager.sessions.values()):
        raise HTTPException(409, 'Stop the agent before editing project files')
    try:
        if not isinstance(body.get('content'), str) or not isinstance(body.get('sha256'), str):
            raise ValueError('Supply text and the file hash from the last read')
        work = SourceWorkspace(_project(project_id))
        work.ensure()
        await work.fs({'action': 'write', 'path': body.get('path'), 'content': body['content'], 'expected_sha256': body['sha256']})
        return await source_file(project_id, body['path'])
    except (ValueError, OSError) as error:
        raise HTTPException(409, str(error)) from error


@app.get('/api/projects/{project_id}/{revision}/{name}')
async def artifact(project_id: str, revision: str, name: str):
    try:
        path = _project(project_id).file(revision, name)
    except ValueError as error:
        raise HTTPException(404, str(error)) from error
    if name.endswith('.png'):
        return FileResponse(path, media_type='image/png')
    return FileResponse(path, filename=f'{project_id}-{revision}-{name}', media_type='application/octet-stream')


@app.post('/api/sessions/{session_id}/project')
async def project_operation(session_id: str, body: dict):
    session = _session(session_id)
    if not session.project:
        raise HTTPException(409, 'This application does not support native projects')
    if (session.agent and session.agent.active) or getattr(session, 'project_busy', False):
        raise HTTPException(409, 'Stop the agent before changing the project base or restoring a revision')
    session.project_busy = True
    try:
        if body.get('operation') == 'use_saved':
            # Explicit acknowledgment only; never save, discard, or close a GUI document.
            await acknowledge_saved(session)
        elif body.get('operation') == 'engine' and isinstance(body.get('engine'), str) and body.get('engine') in {'hybrid', 'visual'}:
            session.engine = body['engine']
        elif body.get('operation') == 'restore':
            session.project.restore(body.get('revision'), body.get('expected_head'))
            try:
                await present_revision(session)
            except Exception as error:
                return session.project.public() | {'warning': 'Revision restored, but the viewport did not open it: ' + str(error)}
        elif body.get('operation') == 'source_build':
            from .workspace_tools import build_revision
            saved, warning = await build_revision(session, body.get('entrypoint'), body.get('expected_head'))
            return saved | ({'warning': warning} if warning else {})
        else:
            raise ValueError('Unknown project operation')
        return session.project.public()
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    finally:
        session.project_busy = False


@app.post('/api/sessions/{session_id}/attachments')
async def upload_attachment(session_id: str, file: UploadFile = File(...)):
    """An image for the model to look at, or a STEP/STL reference model for the project."""
    session = manager.sessions.get(session_id)
    if session is None or not getattr(session, 'project', None):
        raise HTTPException(404, 'No native project is open in this session')
    name = os.path.basename(file.filename or 'upload')
    content = await file.read(41 * 1024 * 1024)
    if len(content) > 40 * 1024 * 1024:
        raise HTTPException(413, 'File exceeds 40 MiB')
    try:
        if name.lower().endswith(('.step', '.stp', '.stl')):
            stored = await asyncio.to_thread(session.project.add_reference, name, content)
            return {'kind': 'reference', 'name': stored}
        from .attachments import store_image
        attachment = await asyncio.to_thread(store_image, session.project.path / 'attachments', content, name)
        return {'kind': 'image', **attachment}
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@app.get('/api/projects/{project_id}/images/{name}')
async def project_image(project_id: str, name: str):
    from .attachments import image_path
    try:
        path = image_path(_project(project_id).path, name)
    except ValueError as error:
        raise HTTPException(404, str(error)) from error
    return FileResponse(path, media_type='image/jpeg')


@app.get("/api/sessions/{session_id}/frame.jpg")
async def frame(session_id: str):
    from fastapi.responses import Response

    session = _session(session_id)
    data = await asyncio.to_thread(session.screen.capture, quality=85)
    return Response(content=data, media_type="image/jpeg")


@app.delete("/api/sessions/{session_id}")
async def close_session(session_id: str) -> dict:
    session = _session(session_id)
    if session.agent:
        await session.agent.wait_stopped()
    await asyncio.to_thread(manager.close, session_id)
    return {"ok": True}


def _session(session_id: str):
    session = manager.sessions.get(session_id)
    if session is None:
        raise HTTPException(404, "no such session")
    return session


@app.get("/api/sessions/{session_id}/activity")
async def activity(session_id: str) -> dict:
    session = _session(session_id)
    return session.agent.snapshot() if session.agent else {"events": [], "active": False}


async def _desktop_input(session, message):
    screen = session.screen
    message = desktop_message(message, screen.width, screen.height)
    kind = message.pop("t")
    if getattr(session, 'project_busy', False):
        raise ValueError('A saved revision is opening; wait before using the viewport')
    if kind in {'click', 'down', 'up', 'scroll', 'key', 'text'}:
        note_input(session)
    if kind == "move":
        await asyncio.to_thread(screen.move, **message)
    elif kind == "click":
        await asyncio.to_thread(screen.click, **message)
    elif kind == "down":
        await asyncio.to_thread(screen.pointer_down, **message)
    elif kind == "up":
        await asyncio.to_thread(screen.pointer_up, **message)
    elif kind == "release":
        await asyncio.to_thread(screen.release_inputs)
    elif kind == "scroll":
        await asyncio.to_thread(screen.scroll, **message)
    elif kind == "key":
        await asyncio.to_thread(screen.key, message["key"], message["modifiers"])
    elif kind == "text":
        await asyncio.to_thread(getattr(screen, 'paste_text', screen.type_text), message["text"])
    if kind in {'click', 'down', 'up', 'scroll', 'key', 'text'}:
        note_input(session)


@app.websocket("/ws/view/{session_id}")
async def view_socket(socket: WebSocket, session_id: str) -> None:
    session = manager.sessions.get(session_id)
    if session is None:
        await socket.close(code=4404)
        return
    await socket.accept()
    fps = float(CONFIG["view"].get("fps", 8))
    quality = int(CONFIG["view"].get("quality", 78))
    max_width = int(CONFIG["view"].get("max_width", 1600))

    async def stream() -> None:
        closed_notified = False
        try:
            while True:
                if session.app_proc.poll() is not None and not closed_notified:
                    closed_notified = True
                    if session.agent and session.agent.active:
                        session.agent.stop("stopped because the CAD application closed")
                    await socket.send_json({"t": "app_closed"})
                frame = await asyncio.to_thread(session.screen.capture, quality=quality, max_width=max_width)
                await socket.send_bytes(frame)
                await asyncio.sleep(1.0 / fps)
        except Exception:
            with contextlib.suppress(Exception):
                await socket.close(code=1011)

    streamer = asyncio.create_task(stream())
    held_buttons = set()
    try:
        while True:
            try:
                message = decode_message(await socket.receive_text())
                if session.agent and session.agent.active:
                    if message["t"] not in {"up", "release"} or not held_buttons:
                        continue
                await _desktop_input(session, message)
                if message["t"] == "down":
                    held_buttons.add(message.get("button", "left"))
                elif message["t"] == "up":
                    held_buttons.discard(message.get("button", "left"))
                elif message["t"] == "release":
                    held_buttons.clear()
            except (ValueError, KeyError, TypeError, RuntimeError) as error:
                await socket.send_json({"t": "input_error", "message": str(error)})
    except (WebSocketDisconnect, RuntimeError, ValueError, KeyError, TypeError):
        pass
    finally:
        if held_buttons:
            with contextlib.suppress(Exception):
                await asyncio.to_thread(session.screen.release_inputs)
        streamer.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await streamer


@app.websocket("/ws/agent/{session_id}")
async def agent_socket(socket: WebSocket, session_id: str) -> None:
    session = manager.sessions.get(session_id)
    if session is None:
        await socket.close(code=4404)
        return
    await socket.accept()
    if session.agent is None:
        session.agent = AgentRunner(session, CONFIG)
    runner = session.agent
    queue = runner.subscribe()
    await socket.send_json(runner.snapshot())

    async def forward() -> None:
        while True:
            await socket.send_text(json.dumps(await queue.get()))

    forwarder = asyncio.create_task(forward())
    try:
        while True:
            try:
                message = decode_message(await socket.receive_text())
                kind = message.get("t")
                if kind == "start":
                    if session.app_proc.poll() is not None:
                        raise ValueError("The CAD application has closed. Start a new session to continue.")
                    if type(message.get("new_task", False)) is not bool:
                        raise ValueError("new_task must be a boolean")
                    await _endpoint_health()
                    runner.start(message.get("task", ""), message.get("mode", "auto"), new_task=message.get("new_task", False),
                                 attachments=message.get("attachments") or [])
                elif kind == "intent":
                    runner.submit_intent(message.get("text", ""), attachments=message.get("attachments") or [])
                elif kind == "answer":
                    runner.answer_question(message.get("question_id"), message.get("selected") or [], message.get("text") or "")
                elif kind == "stop":
                    runner.stop()
                elif kind == "mode":
                    runner.set_mode(message.get("mode"))
                elif kind == 'web_setting':
                    runner.set_web_enabled(message.get('enabled'))
                elif kind in {"pause", "resume"}:
                    runner.pause(kind == "pause")
                else:
                    raise ValueError("Unknown agent command")
            except (ValueError, RuntimeError, TypeError, AttributeError) as error:
                await socket.send_json({"t": "request_error", "message": str(error)})
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        forwarder.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await forwarder
        if runner and queue in runner.subscribers:
            runner.subscribers.remove(queue)


@app.on_event("shutdown")
async def shutdown() -> None:
    await asyncio.gather(*(s.agent.wait_stopped() for s in manager.sessions.values() if s.agent))
    await asyncio.to_thread(manager.close_all)


@app.on_event('startup')
async def start_connector():
    if os.environ.get('CADPILOT_PORTAL_URL'):
        from .connector import connect_runtime, validate_origin
        validate_origin(os.environ['CADPILOT_PORTAL_URL'])
        app.state.connector_status = {'state': 'connecting', 'detail': 'Waiting for the portal connection.'}
        app.state.connector = asyncio.create_task(connect_runtime(app))


@app.on_event('shutdown')
async def stop_connector():
    connector = getattr(app.state, 'connector', None)
    if connector:
        connector.cancel()
        await asyncio.gather(connector, return_exceptions=True)
