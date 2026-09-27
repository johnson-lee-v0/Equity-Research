"""Task-sized Codex defaults, shared by execution and cache identity."""
from ..schemas import ModelConfig

VERSION = "task-model-policy.v2"
_ASSIGNMENTS = {
    "A00": ("gpt-6-luna", "high", "routing"),
    "A01": ("gpt-6-luna", "high", "discovery"),
    "A02": ("gpt-6-luna", "high", "extraction"),
    "A03": ("gpt-6-sol", "high", "researcher"),
    "A04": ("gpt-6-sol", "medium", "technical"),
    "A05": ("gpt-6-sol", "medium", "entry"),
    "A06": ("gpt-6-luna", "high", "monitoring"),
    "A07": ("gpt-6-sol", "medium", "simulation"),
    "A08": ("gpt-6-luna", "high", "extraction"),
    "A09": ("gpt-6-sol", "medium", "macro"),
    "A10": ("gpt-6-sol", "medium", "portfolio"),
    "A11": ("gpt-6-astra", "medium", "committee"),
}


def role_model(agent_id: str) -> ModelConfig:
    model, effort, profile = _ASSIGNMENTS[agent_id]
    return ModelConfig(provider="codex", model=model, reasoning_effort=effort, profile=profile)


def compatible_contract_override(agent_id: str, override: ModelConfig) -> bool:
    """Explicit stronger effort is allowed without weakening the review role."""
    expected = role_model(agent_id)
    efforts = ("low", "medium", "high", "xhigh", "max", "ultra")
    return (override.provider == expected.provider and override.model == expected.model
            and override.reasoning_effort in efforts
            and efforts.index(override.reasoning_effort) >= efforts.index(expected.reasoning_effort)
            and not (override.model == "gpt-6-luna" and override.reasoning_effort == "ultra"))
