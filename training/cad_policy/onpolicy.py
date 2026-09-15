"""Single-update on-policy group-relative REINFORCE helpers (not offline replay/SFT)."""
import torch
import torch.nn.functional as F

from .loss import unwrap_causal_lm


def advantages(rewards):
    values = torch.tensor(rewards, dtype=torch.float32)
    if len(values) < 2:
        raise ValueError("At least two fresh rollouts per task are required")
    # Leave-one-out baseline: other independent attempts at the exact same task.
    return values - (values.sum() - values) / (len(values) - 1)


def completion_logprobs(model, inputs, generated, temperature):
    inputs = dict(inputs)
    prompt_length = inputs["input_ids"].shape[1]
    inputs["input_ids"] = torch.cat([inputs["input_ids"], generated], dim=1)
    inputs["attention_mask"] = torch.ones_like(inputs["input_ids"])
    if "mm_token_type_ids" in inputs:
        inputs["mm_token_type_ids"] = torch.cat([inputs["mm_token_type_ids"], torch.zeros_like(generated)], dim=1)
    causal = unwrap_causal_lm(model)
    hidden = causal._modules["model"](**inputs, use_cache=False, return_dict=True).last_hidden_state
    positions = hidden[:, prompt_length - 1:-1]
    logits = causal._modules["lm_head"](positions).float() / temperature
    return F.log_softmax(logits, dim=-1).gather(-1, generated.unsqueeze(-1)).squeeze(-1)


def policy_gradient_loss(logprobs, reference_logprobs, advantage, *, beta=.01):
    delta = reference_logprobs.detach() - logprobs
    kl = torch.exp(delta.clamp(-20, 20)) - delta - 1
    # Sum over emitted tokens gives a trajectory log likelihood, not imitation of selected wins.
    return -advantage * logprobs.sum() + beta * kl.mean()
