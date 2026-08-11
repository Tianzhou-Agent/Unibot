from tianzhou_agent_platform.core.context_compression import estimate_request_tokens
from tianzhou_agent_platform.core.observability_query import ObsQueryService


def _model_span_row(**overrides):
    row = {
        "span_id": "otel-span",
        "legacy_span_id": "span-model",
        "trace_id": "trace-1",
        "parent_span_id": "span-root",
        "sequence_no": 1,
        "kind": "model",
        "name": "chat.completions",
        "target_id": None,
        "model": "test-model",
        "status": "completed",
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "input_preview": '{"messages":[{"role":"user","content":"hello"}],"estimated_prompt_tokens":17}',
        "output_preview": '{"role":"assistant","content":"hello back"}',
        "attributes": {"provider": "test"},
    }
    row.update(overrides)
    return row


def test_span_dto_estimates_historical_missing_model_usage() -> None:
    dto = ObsQueryService._span_dto(_model_span_row())

    assert dto["input_tokens"] == 17
    assert dto["output_tokens"] == estimate_request_tokens([dto["output"]])
    assert dto["attributes"] == {
        "provider": "test",
        "usage_estimated": True,
        "usage_source": "estimated",
    }


def test_span_dto_preserves_reported_usage() -> None:
    dto = ObsQueryService._span_dto(_model_span_row(input_tokens=10, output_tokens=5))

    assert dto["input_tokens"] == 10
    assert dto["output_tokens"] == 5
    assert dto["attributes"] == {"provider": "test"}
