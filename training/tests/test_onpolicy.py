import torch
from cad_policy.onpolicy import advantages, policy_gradient_loss
from cad_policy.cad_tasks import make_task


def test_leave_one_out_baseline_and_no_signal():
    assert torch.equal(advantages([0, 0, 0, 0]), torch.zeros(4))
    assert torch.allclose(advantages([1, 0, 0, 0]), torch.tensor([1., -1/3, -1/3, -1/3]))


def test_policy_gradient_increases_rewarded_and_decreases_failed_log_probability():
    for advantage in [1., -1.]:
        p = torch.tensor([[-1., -2.]], requires_grad=True)
        policy_gradient_loss(p, p.detach().clone(), advantage).backward()
        assert torch.equal(p.grad.sign(), torch.full_like(p, -advantage))


def test_disjoint_task_parameter_seeds():
    train = [make_task(i, app="freecad") for i in range(10)]
    held = [make_task(i, app="freecad", split="validation") for i in range(10)]
    assert [t["id"] for t in train] != [t["id"] for t in held]
    assert [t["prompt"] for t in train] != [t["prompt"] for t in held]
