"""Bounded fresh-policy CAD rollouts followed by exactly one update per rollout group.

Uses the current adapter directly for generation (no external planner or teacher), live GUI
execution, and sandboxed native-geometry rewards. Each group is discarded after its update.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import torch
from PIL import Image

from cad1000.policy_view import parse_completion, serialize_target
from cad1000.trajectory_view import build_trajectory_messages, log_line
from cad_policy.config import load_config
from cad_policy.data import dump_json, ImageTokenizer
from cad_policy.model import load_base_model, load_processor
from cad_policy.trajectory import render_conversation
from cad_policy.cad_tasks import make_task
from cad_policy.cad_environment import CADEnvironment
from cad_policy.onpolicy import advantages, completion_logprobs, policy_gradient_loss


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="training/configs/v3.yaml")
    parser.add_argument("--adapter", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--evaluate-only", action="store_true")
    parser.add_argument("--groups", type=int, default=4)
    parser.add_argument("--group-size", type=int, default=4)
    parser.add_argument("--max-steps", type=int, default=32)
    parser.add_argument("--seed", type=int, default=20260908)
    parser.add_argument("--temperature", type=float, default=.7)
    parser.add_argument("--lr", type=float, default=2e-6)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Use a new output directory; on-policy trials must not reuse stale trajectories")
    if not 1 <= args.groups <= 32 or not 2 <= args.group_size <= 8 or not 1 <= args.max_steps <= 80 or args.temperature <= 0:
        raise SystemExit("Invalid bounded rollout configuration")
    args.output.mkdir(parents=True)
    config = load_config(args.config)
    torch.manual_seed(args.seed)
    processor = load_processor(config["model"])
    ImageTokenizer(processor, min_pixels=65536, max_pixels=1048576)
    from peft import PeftModel
    model = PeftModel.from_pretrained(load_base_model(config["model"], training=False), args.adapter, is_trainable=not args.evaluate_only)
    if not args.evaluate_only:
        model.load_adapter(args.adapter, adapter_name="reference", is_trainable=False)
        model.set_adapter("default")
    model.eval()  # Disable dropout for generation AND the matching policy likelihood.
    device = next(model.parameters()).device
    optimizer = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=args.lr) if not args.evaluate_only else None
    end_id = processor.tokenizer.convert_tokens_to_ids("<|im_end|>")
    results, stalled, levels = [], 0, {"freecad": "box", "openscad": "box"}
    dump_json(args.output / "manifest.json", {"algorithm": "on-policy-group-REINFORCE-v1", "args": vars(args),
              "config": config, "reference_adapter": str(Path(args.adapter).resolve()), "started": time.time(),
              "updates_per_fresh_group": 1, "teacher_actions": False, "replay": False})

    def encode(text, path):
        with Image.open(path) as image:
            encoded = processor(text=[text], images=[image.convert("RGB")], return_tensors="pt")
        return {k: v.to(device=device, dtype=torch.bfloat16) if k == "pixel_values" else v.to(device) for k, v in encoded.items()}

    for group in range(args.groups):
        app = ("freecad", "openscad")[group % 2]
        difficulty = "plate" if args.evaluate_only and group >= 2 else levels[app]
        task = make_task(args.seed + group, app=app, split="validation" if args.evaluate_only else "train", difficulty=difficulty)
        trials, rewards = [], []
        for attempt in range(args.group_size):
            directory = args.output / f"group-{group:03d}" / f"attempt-{attempt:02d}"
            directory.mkdir(parents=True)
            steps, history = [], []
            env = CADEnvironment(task, directory)
            started = time.monotonic()
            outcome = {"reward": 0.0, "success": False, "reason": "step_budget"}
            try:
                for number in range(args.max_steps):
                    if time.monotonic() - started > 600:
                        break
                    path = directory / f"frame-{number:03d}.jpg"
                    env.observe().save(path, quality=90)
                    record = {"task": {"application": app, "task": task["prompt"]}, "first_step_number": number + 1,
                              "history_lines": history[-32:], "omitted_history_steps": max(0, len(history) - 32),
                              "steps": [{"intent": task["prompt"], "completion": ""}]}
                    text = render_conversation(build_trajectory_messages(record), drop_final_assistant=True)
                    encoded = encode(text, path)
                    if encoded["input_ids"].shape[1] > 3840:
                        outcome = {"reward": 0.0, "success": False, "reason": "context_budget"}
                        break
                    with torch.no_grad():
                        tokens = model.generate(**encoded, max_new_tokens=256, do_sample=True, temperature=args.temperature,
                                                top_p=1.0, top_k=0, repetition_penalty=1.0, use_cache=True,
                                                pad_token_id=processor.tokenizer.pad_token_id, eos_token_id=[end_id, processor.tokenizer.eos_token_id])
                    generated = tokens[:, encoded["input_ids"].shape[1]:].detach().cpu()
                    response = processor.tokenizer.decode(generated[0], skip_special_tokens=True)
                    steps.append({"text": text, "image": str(path), "tokens": generated.tolist(), "response": response})
                    actions, error = parse_completion(response)
                    if error or len(actions) > 4 or generated[0, -1].item() not in {end_id, processor.tokenizer.eos_token_id}:
                        outcome = {"reward": 0.0, "success": False, "reason": "invalid_or_truncated_action"}
                        break
                    env.step(actions)
                    history.append(log_line(number + 1, {"intent": task["prompt"], "completion": serialize_target(actions)}))
                    if (env.work / task["artifact"]).exists():
                        outcome = env.reward()
                        if outcome["success"]:
                            break
            finally:
                env.close()
            dump_json(directory / "rollout.json", {"policy_version": group, "task": task, "steps": steps, "outcome": outcome})
            trials.append(steps)
            rewards.append(outcome["reward"])
            print(json.dumps({"group": group, "attempt": attempt, **outcome}), flush=True)
        adv = advantages(rewards)
        updated = False
        if not args.evaluate_only and adv.abs().max().item() > 1e-8:
            optimizer.zero_grad(set_to_none=True)
            for steps, advantage in zip(trials, adv):
                for step in steps:
                    encoded = encode(step["text"], step["image"])
                    generated = torch.tensor(step["tokens"], device=device)
                    model.set_adapter("reference")
                    with torch.no_grad():
                        reference = completion_logprobs(model, encoded, generated, args.temperature)
                    model.set_adapter("default")
                    current = completion_logprobs(model, encoded, generated, args.temperature)
                    loss = policy_gradient_loss(current, reference, advantage.to(device)) / args.group_size
                    loss.backward()
            torch.nn.utils.clip_grad_norm_([p for p in model.parameters() if p.requires_grad], 1.0)
            optimizer.step()
            updated = True
            stalled = 0
            model.save_pretrained(args.output / f"checkpoint-{group + 1}", selected_adapters=["default"])
        elif not args.evaluate_only:
            stalled += 1
        results.append({"group": group, "task": task["id"], "rewards": rewards, "updated": updated})
        if not args.evaluate_only and sum(r == 1 for r in rewards) >= args.group_size / 2:
            levels[app] = "plate"
        dump_json(args.output / "progress.json", results)
        # All-zero rewards provide no learning signal. Report this, rather than claiming an
        # RL improvement or manufacturing successful demonstrations.
        if stalled >= 3:
            break
    if not args.evaluate_only:
        model.save_pretrained(args.output / "adapter", selected_adapters=["default"])
        processor.save_pretrained(args.output / "adapter")
    dump_json(args.output / "summary.json", {"groups": results, "updates": sum(r["updated"] for r in results),
              "status": "evaluated" if args.evaluate_only else "no_reward_variance" if stalled >= 3 else "trial_complete",
              "promotion": "requires_disjoint_task_evaluation"})


if __name__ == "__main__":
    main()
