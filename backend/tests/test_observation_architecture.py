from pathlib import Path


def test_agent_runtime_has_no_observability_dependency() -> None:
    source = (
        Path(__file__).parents[1]
        / "src"
        / "tianzhou_agent_platform"
        / "core"
        / "agent.py"
    ).read_text(encoding="utf-8")

    forbidden = (
        "observation",
        "observability",
        "record_event(",
        ".emit(",
        "AgentRunObserver",
        "AgentEventPublisher",
        "ObservedLLMClient",
        "ObservedCapabilityGateway",
    )

    assert all(token not in source for token in forbidden)
