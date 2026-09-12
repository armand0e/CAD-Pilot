"""Command-line entry point."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .hf import DEFAULT_REPO, DEFAULT_REVISION, sync_workflows
from .policy_view import convert_file, validate_policy_records
from .prepare import prepare_dataset, validate_records
from .shards import ALL_SOFTWARE, build_shards, merge_shards
from .trajectory_view import convert_trajectories, validate_trajectory_records


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cad1000", description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    sync = subparsers.add_parser("sync", help="Download a safe, selective dataset shard")
    sync.add_argument("--root", type=Path, default=Path("data/raw"))
    sync.add_argument("--repo", default=DEFAULT_REPO)
    sync.add_argument("--revision", default=DEFAULT_REVISION)
    sync.add_argument("--software", default="solidworks")
    sync.add_argument("--workflow", action="append", dest="workflows")
    sync.add_argument("--max-workflows", type=int, default=1)
    sync.add_argument("--include-video", action="store_true")
    sync.add_argument("--include-frame-events", action="store_true")
    sync.add_argument("--include-inputs", action="store_true")
    sync.add_argument("--include-deliverables", action="store_true")

    prepare = subparsers.add_parser("prepare", help="Build compact trajectory and chat JSONL")
    prepare.add_argument("--input", type=Path, default=Path("data/raw"))
    prepare.add_argument("--output", type=Path, default=Path("data/processed/v1"))
    _add_prepare_options(prepare)
    prepare.add_argument("--max-examples", type=int)
    prepare.add_argument("--extract-frames", action="store_true")

    shards = subparsers.add_parser(
        "build-shards",
        help="Restartable per-workflow download -> prepare -> validate -> cleanup loop",
    )
    shards.add_argument("--raw-root", type=Path, default=Path("data/raw"))
    shards.add_argument("--shard-root", type=Path, default=Path("data/shards"))
    shards.add_argument("--repo", default=DEFAULT_REPO)
    shards.add_argument("--revision", default=DEFAULT_REVISION)
    shards.add_argument(
        "--software",
        action="append",
        dest="softwares",
        choices=ALL_SOFTWARE,
        help="Restrict to one or more applications (default: all, round-robin order)",
    )
    shards.add_argument("--workflow", action="append", dest="workflows", help="software/workflow-id")
    shards.add_argument("--max-workflows-per-software", type=int)
    shards.add_argument("--max-workflows", type=int, help="Stop after processing this many new workflows")
    shards.add_argument("--min-free-gib", type=float, default=40.0)
    shards.add_argument("--no-cleanup", action="store_true", help="Keep clip.mp4/events.json after validation")
    shards.add_argument("--no-inputs", action="store_true")
    shards.add_argument("--no-deliverables", action="store_true")
    _add_prepare_options(shards)

    merge = subparsers.add_parser("merge-shards", help="Concatenate validated shards into one corpus")
    merge.add_argument("--shard-root", type=Path, default=Path("data/shards"))
    merge.add_argument("--output", type=Path, default=Path("data/processed/corpus"))
    merge.add_argument("--no-verify", action="store_true")

    policy = subparsers.add_parser(
        "policy-view", help="Derive short executable 0..999-grid policy targets from neutral JSONL"
    )
    policy.add_argument("--input", type=Path, required=True, help="Neutral all.jsonl (frames beside it)")
    policy.add_argument("--output", type=Path, required=True)
    policy.add_argument("--max-chunk", type=int, default=1, help="Maximum actions per target (1-4)")
    policy.add_argument("--max-gap-s", type=float, default=1.0, help="Split chunks at pauses longer than this")
    policy.add_argument("--drag-mode", choices=("exclude", "include"), default="exclude")

    trajectory = subparsers.add_parser(
        "trajectory-view", help="Long-context windows of consecutive steps with screenshots and a text log"
    )
    trajectory.add_argument("--input", type=Path, required=True, help="Neutral all.jsonl (frames beside it)")
    trajectory.add_argument("--output", type=Path, required=True)
    trajectory.add_argument("--budget-tokens", type=int, default=65536)
    trajectory.add_argument("--max-chunk", type=int, default=1)
    trajectory.add_argument("--max-gap-s", type=float, default=1.0)
    trajectory.add_argument("--drag-mode", choices=("exclude", "include"), default="exclude")
    trajectory.add_argument("--history-fraction", type=float, default=0.2, help="Share of the budget reserved for the text log")
    trajectory.add_argument("--max-windows-per-workflow", type=int)

    validate = subparsers.add_parser("validate", help="Validate JSONL schema invariants and leakage")
    validate.add_argument("paths", nargs="+", type=Path)
    validate.add_argument("--policy", action="store_true", help="Validate cad-policy rows instead")
    validate.add_argument("--trajectory", action="store_true", help="Validate cad-trajectory rows instead")
    return parser


def _add_prepare_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--min-quality", type=float, default=0.9)
    parser.add_argument("--max-segment-s", type=float, default=30.0)
    parser.add_argument("--max-actions", type=int, default=128)
    parser.add_argument("--observation-lead-s", type=float, default=0.5)
    parser.add_argument("--train-ratio", type=float, default=0.9)
    parser.add_argument("--validation-ratio", type=float, default=0.05)


def _prepare_options(args: argparse.Namespace) -> dict:
    return {
        "min_quality": args.min_quality,
        "max_segment_s": args.max_segment_s,
        "max_actions": args.max_actions,
        "observation_lead_s": args.observation_lead_s,
        "train_ratio": args.train_ratio,
        "validation_ratio": args.validation_ratio,
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "sync":
        records = sync_workflows(
            args.root,
            repo=args.repo,
            revision=args.revision,
            software=args.software,
            workflow_ids=args.workflows,
            max_workflows=args.max_workflows,
            include_video=args.include_video,
            include_frame_events=args.include_frame_events,
            include_inputs=args.include_inputs,
            include_deliverables=args.include_deliverables,
        )
        manifest = args.root / "source_manifest.json"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"downloaded_workflows": len(records), "manifest": str(manifest)}, indent=2))
        return 0
    if args.command == "prepare":
        report = prepare_dataset(
            args.input,
            args.output,
            max_examples=args.max_examples,
            extract_frames=args.extract_frames,
            **_prepare_options(args),
        )
        print(json.dumps(report, indent=2))
        return 0
    if args.command == "build-shards":
        summary = build_shards(
            args.raw_root,
            args.shard_root,
            repo=args.repo,
            revision=args.revision,
            softwares=args.softwares or ALL_SOFTWARE,
            workflow_ids=args.workflows,
            max_per_software=args.max_workflows_per_software,
            max_workflows=args.max_workflows,
            min_free_gib=args.min_free_gib,
            cleanup=not args.no_cleanup,
            include_inputs=not args.no_inputs,
            include_deliverables=not args.no_deliverables,
            prepare_options=_prepare_options(args),
        )
        print(json.dumps(summary, indent=2))
        return 0 if summary["failed"] == 0 else 1
    if args.command == "merge-shards":
        print(json.dumps(merge_shards(args.shard_root, args.output, verify=not args.no_verify), indent=2))
        return 0
    if args.command == "policy-view":
        report = convert_file(
            args.input,
            args.output,
            max_chunk=args.max_chunk,
            max_gap_s=args.max_gap_s,
            drag_mode=args.drag_mode,
        )
        print(json.dumps(report, indent=2))
        return 0
    if args.command == "trajectory-view":
        report = convert_trajectories(
            args.input,
            args.output,
            budget_tokens=args.budget_tokens,
            max_chunk=args.max_chunk,
            max_gap_s=args.max_gap_s,
            drag_mode=args.drag_mode,
            history_fraction=args.history_fraction,
            max_windows_per_workflow=args.max_windows_per_workflow,
        )
        print(json.dumps(report, indent=2))
        return 0
    if args.command == "validate":
        validator = validate_records
        if args.policy:
            validator = validate_policy_records
        if args.trajectory:
            validator = validate_trajectory_records
        print(json.dumps(validator(args.paths), indent=2))
        return 0
    raise AssertionError(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
