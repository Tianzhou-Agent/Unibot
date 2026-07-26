import pytest

from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.core.schema import validate_schema, validate_value


def test_portable_json_schema_constraints_accept_valid_value() -> None:
    schema = {
        "type": "object",
        "properties": {
            "timeout": {"type": "integer", "minimum": 1, "maximum": 300},
            "script": {"type": "string", "minLength": 1, "maxLength": 20, "pattern": r"^print"},
            "tags": {
                "type": "array",
                "minItems": 1,
                "maxItems": 2,
                "items": {"type": "string"},
            },
        },
        "required": ["timeout", "script", "tags"],
        "additionalProperties": False,
    }

    validate_schema(schema, label="input")
    validate_value(
        {"timeout": 60, "script": "print('ok')", "tags": ["safe"]},
        schema,
        label="input",
    )


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ({"timeout": 0, "script": "print('ok')", "tags": ["safe"]}, "at least 1"),
        ({"timeout": 301, "script": "print('ok')", "tags": ["safe"]}, "at most 300"),
        ({"timeout": 60, "script": "", "tags": ["safe"]}, "at least 1"),
        ({"timeout": 60, "script": "raise Error", "tags": ["safe"]}, "required pattern"),
        ({"timeout": 60, "script": "print('ok')", "tags": []}, "at least 1"),
    ],
)
def test_portable_json_schema_constraints_reject_invalid_value(
    value: dict[str, object],
    message: str,
) -> None:
    schema = {
        "type": "object",
        "properties": {
            "timeout": {"type": "integer", "minimum": 1, "maximum": 300},
            "script": {"type": "string", "minLength": 1, "pattern": r"^print"},
            "tags": {"type": "array", "minItems": 1},
        },
    }

    with pytest.raises(PlatformError, match=message):
        validate_value(value, schema, label="input")


def test_invalid_schema_bounds_and_pattern_are_rejected() -> None:
    with pytest.raises(PlatformError, match="minLength must not exceed maxLength"):
        validate_schema(
            {"type": "string", "minLength": 5, "maxLength": 2},
            label="input",
        )
    with pytest.raises(PlatformError, match="pattern is invalid"):
        validate_schema({"type": "string", "pattern": "["}, label="input")
