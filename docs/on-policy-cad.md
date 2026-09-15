# On-policy CAD stage

This is an implemented experimental training path, not a claim that RL has improved the model.
The user requested fresh-policy computer-use/CAD learning after the corrected supervised run.

## Algorithm and boundaries

`training/train_onpolicy.py` loads the selected SFT adapter and generates all GUI actions itself.
There is no planner/teacher model and no stored demonstration replay. For each task it collects
four fresh independent attempts with unchanged weights, computes a leave-one-out terminal
reward baseline, accumulates the policy-gradient loss, takes exactly one optimizer step, and
never uses those trajectories again. Generation and likelihood use the same temperature and
no top-k/top-p truncation. Dropout is disabled in both paths. A frozen copy of the SFT adapter
provides sampled KL regularization; gradient clipping and a small learning rate bound updates.
This is group-relative REINFORCE, not a claim of reproducing a particular GRPO implementation.

Rollouts retain screenshots, exact prompts and generated token IDs, policy version, actions and
native reward evidence. Invalid/truncated JSON is not repaired or silently replaced by teacher
actions. Equal rewards produce no policy-gradient signal; three consecutive such groups end
the trial with `no_reward_variance`, not “training succeeded.”

The first curriculum is native solid blocks, then four-hole plates after the current app achieves
50% task success in a group. At most 32 groups × 8 attempts × 80 actions can be requested by one
invocation; defaults are 4 × 4 × 32. Each episode also has a ten-minute action-loop deadline.
Validation and test task dimensions are disjoint from the training dimension set. They never
enter RL updates. The initial transfer trial runs before adding FreeCAD/OpenSCAD demonstrations.

## Reward and containment

CAD runs in a fresh virtual display and bubblewrap namespaces, without network, user home,
project checkout, other X sockets, or grader access. Only the installed application and system
libraries are readable; only `/work` is persistent/writable. The sandbox has an address-space
and CPU-time limit. Run the outer training service with cgroup memory/task limits too; these
namespaces and per-process limits alone are not a complete resource-denial defense.

The host snapshots the submitted native artifact and grades it in a separate sandbox. FreeCAD
checks a recomputed single valid solid. OpenSCAD first renders the submitted `.scad` into a mesh.
Both are compared with reference geometry using bounding boxes and symmetric-difference
volume. Hole-scale tolerance prevents a missing hole being hidden by the block's much larger
volume. Analytic BRep and tessellated meshes use different tolerances. A terminal success bonus
is reserved for satisfying both checks; valid but wrong geometry gets bounded partial credit.

Eight integration fixtures pass: each app accepts the correct plate and rejects missing holes,
misplaced holes, and wrong dimensions. These are trusted reward tests, not SFT demonstrations.
No artifact test establishes general modeling skill beyond the tasks it measures. Add more
task families and adversarial grader cases before expanding or promoting the RL stage.

## Execution

Use the training venv, with `python-xlib==0.33`, and an available parent DISPLAY until Xvfb is
installed. The single GPU cannot host the stock 27B inference server and this training job at
once. Preserve user CAD work; stop/restore only the managed inference endpoint, not the harness.

```bash
# Fresh transfer baseline: no updates, no synthetic demonstrations.
/home/armand0e/Documents/training/.venv/bin/python training/train_onpolicy.py \
  --adapter runs/cad-policy-v3-pilot/adapter --output runs/transfer-v3-pilot --evaluate-only

# Bounded fresh-policy trial after a valid SFT checkpoint exists.
/home/armand0e/Documents/training/.venv/bin/python training/train_onpolicy.py \
  --adapter runs/cad-policy-v3/adapter --output runs/onpolicy-v3-trial

# Must compare a trial adapter to its SFT predecessor on identical disjoint task seeds.
# Do not promote on training reward alone, or use a changed test set after inspecting results.
```

References used while implementing the pipeline: [PyAV frame presentation timestamps](https://pyav.org/docs/stable/api/frame.html),
[FreeCAD shape operations](https://freecad.github.io/API/d8/ded/classPart_1_1TopoShape.html), and
[TRL's public GRPO implementation](https://github.com/huggingface/trl/blob/main/trl/trainer/grpo_trainer.py)
as a comparison for on-policy rollout/update separation, not as the implementation used here.
