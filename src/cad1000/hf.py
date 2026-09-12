"""Small, dependency-free Hugging Face repository client."""

from __future__ import annotations

import json
import os
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, TypeVar

DEFAULT_REPO = "markov-ai/cad-1000-hours"
DEFAULT_REVISION = "b1bd4711343017fc40dfa6fe40a6cd338b7edaf6"
RETRY_ATTEMPTS = 5
RETRY_BACKOFF_S = 3.0
_T = TypeVar("_T")


def _request(url: str) -> urllib.request.Request:
    headers = {"User-Agent": "cad1000-data/0.1"}
    token = os.environ.get("HF_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return urllib.request.Request(url, headers=headers)


def _is_transient(error: BaseException) -> bool:
    if isinstance(error, urllib.error.HTTPError):
        return error.code in {408, 425, 429, 500, 502, 503, 504}
    return isinstance(error, (urllib.error.URLError, ConnectionError, TimeoutError, OSError))


def with_retries(operation: Callable[[], _T], *, attempts: int = RETRY_ATTEMPTS, backoff_s: float = RETRY_BACKOFF_S, sleep: Callable[[float], None] = time.sleep) -> _T:
    """Run ``operation`` with exponential backoff on transient network errors."""
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except Exception as error:
            if attempt == attempts or not _is_transient(error):
                raise
            sleep(backoff_s * 2 ** (attempt - 1))
    raise AssertionError("unreachable")


def get_json(url: str) -> Any:
    def fetch() -> Any:
        with urllib.request.urlopen(_request(url), timeout=60) as response:
            return json.load(response)

    return with_retries(fetch)


def list_tree(
    repo: str,
    revision: str,
    path: str = "",
    *,
    recursive: bool = False,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    encoded_repo = "/".join(urllib.parse.quote(part, safe="") for part in repo.split("/"))
    encoded_revision = urllib.parse.quote(revision, safe="")
    encoded_path = "/".join(urllib.parse.quote(part, safe="") for part in path.split("/") if part)
    suffix = f"/{encoded_path}" if encoded_path else ""
    query = urllib.parse.urlencode(
        {"recursive": str(recursive).lower(), "expand": "false", "limit": limit}
    )
    url = f"https://huggingface.co/api/datasets/{encoded_repo}/tree/{encoded_revision}{suffix}?{query}"
    result = get_json(url)
    if not isinstance(result, list):
        raise TypeError(f"Unexpected Hugging Face tree response for {path!r}")
    return result


def list_workflows(repo: str, revision: str, software: str) -> list[str]:
    entries = list_tree(repo, revision, software)
    workflows = [entry["path"] for entry in entries if entry.get("type") == "directory"]
    return sorted(workflows)


def resolve_url(repo: str, revision: str, repo_path: str) -> str:
    encoded_repo = "/".join(urllib.parse.quote(part, safe="") for part in repo.split("/"))
    encoded_revision = urllib.parse.quote(revision, safe="")
    encoded_path = "/".join(urllib.parse.quote(part, safe="") for part in repo_path.split("/"))
    return f"https://huggingface.co/datasets/{encoded_repo}/resolve/{encoded_revision}/{encoded_path}"


def download_file(repo: str, revision: str, repo_path: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".partial")

    def fetch() -> None:
        try:
            with (
                urllib.request.urlopen(_request(resolve_url(repo, revision, repo_path)), timeout=120) as response,
                temporary.open("wb") as output,
            ):
                shutil.copyfileobj(response, output, length=1024 * 1024)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)

    with_retries(fetch)


def sync_workflows(
    root: Path,
    *,
    repo: str,
    revision: str,
    software: str,
    workflow_ids: Iterable[str] | None,
    max_workflows: int,
    include_video: bool,
    include_frame_events: bool,
    include_inputs: bool,
    include_deliverables: bool,
) -> list[dict[str, Any]]:
    requested = list(workflow_ids or [])
    if requested:
        workflow_paths = [
            item if "/" in item else f"{software}/{item}"
            for item in requested
        ]
    else:
        workflow_paths = list_workflows(repo, revision, software)[:max_workflows]

    base_names = [
        "metadata.json",
        "task_desc.json",
        "rubrics.json",
        "narration.json",
        "events.json",
        "task_overview.pdf",
    ]
    if include_frame_events:
        base_names.append("frame_events.json")
    if include_video:
        base_names.append("clip.mp4")

    records: list[dict[str, Any]] = []
    for workflow_path in workflow_paths:
        entries = list_tree(repo, revision, workflow_path, recursive=True)
        available = {entry["path"]: entry for entry in entries if entry.get("type") == "file"}
        selected_paths = [f"{workflow_path}/{name}" for name in base_names]
        if include_inputs:
            selected_paths.extend(
                path for path in available if path.startswith(f"{workflow_path}/input_files/")
            )
        if include_deliverables:
            selected_paths.extend(
                path for path in available if path.startswith(f"{workflow_path}/output_files/")
            )
        selected_paths = sorted(set(selected_paths))
        missing = [path for path in selected_paths if path not in available]
        if missing:
            raise FileNotFoundError(f"Workflow {workflow_path} is missing: {', '.join(missing)}")

        local_workflow = root / workflow_path
        for repo_path in selected_paths:
            destination = root / repo_path
            expected_size = int(available[repo_path].get("size", 0))
            if destination.exists() and (not expected_size or destination.stat().st_size == expected_size):
                continue
            download_file(repo, revision, repo_path, destination)

        records.append(
            {
                "repo": repo,
                "revision": revision,
                "software": workflow_path.split("/", 1)[0],
                "workflow_id": workflow_path.split("/", 1)[1],
                "path": str(local_workflow),
                "files": [
                    {
                        "name": Path(path).name,
                        "size": int(available[path].get("size", 0)),
                        "oid": available[path].get("oid"),
                    }
                    for path in selected_paths
                ],
            }
        )
    return records
