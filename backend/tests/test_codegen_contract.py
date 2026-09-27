"""The model output contract excludes code-owned scenario calculations."""
from backend.app.providers.codex import normalize_output_schema
from backend.app.schemas import AgentOutputPayload


def test_model_schema_cannot_author_scenario_snapshots() -> None:
    original = AgentOutputPayload.model_json_schema()
    normalized = normalize_output_schema(original)
    assert normalized["properties"]["simulation_snapshot"] == {"type": "null"}
    assert normalized["properties"]["simulation_snapshots"]["maxItems"] == 0
    assert "PriceScenarioSnapshot" not in normalized["$defs"]
    assert "PriceScenarioSnapshot" in original["$defs"]
    assert "anyOf" in original["properties"]["simulation_snapshot"]

    def validate(value):
        if isinstance(value, dict):
            if value.get("type") == "object":
                assert value.get("additionalProperties") is False
                assert set(value.get("required", [])) == set(value.get("properties", {}))
            if "$ref" in value:
                assert value["$ref"].removeprefix("#/$defs/") in normalized["$defs"]
            for item in value.values():
                validate(item)
        elif isinstance(value, list):
            for item in value:
                validate(item)

    validate(normalized)


def test_normalization_preserves_a_non_agent_output_schema() -> None:
    probe = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "additionalProperties": False}
    assert normalize_output_schema(probe) == {**probe, "required": ["ok"]}
    assert "required" not in probe
