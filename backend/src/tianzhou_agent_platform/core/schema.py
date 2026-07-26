from __future__ import annotations

import re
from typing import Any

from tianzhou_agent_platform.core.errors import PlatformError

_JSON_TYPES = {"object", "array", "string", "number", "integer", "boolean", "null"}


def validate_schema(schema: dict[str, Any], *, label: str) -> None:
    """Validate the JSON Schema subset used by the MVP runtime.

    The runtime intentionally supports the portable validation subset used by
    function calling. Unknown annotation keywords remain allowed.
    """

    if not isinstance(schema, dict):
        raise PlatformError("INVALID_REQUEST", f"{label} must be a JSON object")

    schema_type = schema.get("type")
    if schema_type is not None and schema_type not in _JSON_TYPES:
        raise PlatformError("INVALID_REQUEST", f"{label}.type is not supported: {schema_type!r}")

    enum = schema.get("enum")
    if enum is not None and not isinstance(enum, list):
        raise PlatformError("INVALID_REQUEST", f"{label}.enum must be an array")

    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        raise PlatformError("INVALID_REQUEST", f"{label}.properties must be an object")
    for name, child in properties.items():
        if not isinstance(name, str) or not name:
            raise PlatformError("INVALID_REQUEST", f"{label}.properties contains an invalid name")
        validate_schema(child, label=f"{label}.properties.{name}")

    required = schema.get("required", [])
    if not isinstance(required, list) or any(not isinstance(item, str) for item in required):
        raise PlatformError("INVALID_REQUEST", f"{label}.required must be a string array")
    if properties and any(item not in properties for item in required):
        raise PlatformError("INVALID_REQUEST", f"{label}.required references an unknown property")

    if "items" in schema:
        if not isinstance(schema["items"], dict):
            raise PlatformError("INVALID_REQUEST", f"{label}.items must be an object")
        validate_schema(schema["items"], label=f"{label}.items")

    for keyword in ("minLength", "maxLength", "minItems", "maxItems", "minProperties", "maxProperties"):
        if keyword not in schema:
            continue
        value = schema[keyword]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise PlatformError("INVALID_REQUEST", f"{label}.{keyword} must be a non-negative integer")

    for keyword in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"):
        if keyword not in schema:
            continue
        value = schema[keyword]
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise PlatformError("INVALID_REQUEST", f"{label}.{keyword} must be a number")

    if "pattern" in schema:
        pattern = schema["pattern"]
        if not isinstance(pattern, str):
            raise PlatformError("INVALID_REQUEST", f"{label}.pattern must be a string")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise PlatformError("INVALID_REQUEST", f"{label}.pattern is invalid: {exc}") from exc

    _validate_bound_order(schema, "minLength", "maxLength", label)
    _validate_bound_order(schema, "minItems", "maxItems", label)
    _validate_bound_order(schema, "minProperties", "maxProperties", label)
    _validate_bound_order(schema, "minimum", "maximum", label)


def validate_value(value: Any, schema: dict[str, Any], *, label: str) -> None:
    schema_type = schema.get("type")
    valid = {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
        None: True,
    }.get(schema_type, True)
    if not valid:
        raise PlatformError("INVALID_REQUEST", f"{label} must be of type {schema_type}")

    if "enum" in schema and value not in schema["enum"]:
        raise PlatformError("INVALID_REQUEST", f"{label} is not one of the allowed values")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        _validate_numeric_bound(value, schema, "minimum", label, inclusive=True, lower=True)
        _validate_numeric_bound(value, schema, "maximum", label, inclusive=True, lower=False)
        _validate_numeric_bound(value, schema, "exclusiveMinimum", label, inclusive=False, lower=True)
        _validate_numeric_bound(value, schema, "exclusiveMaximum", label, inclusive=False, lower=False)

    if isinstance(value, str):
        _validate_length(value, schema, "minLength", label, minimum=True)
        _validate_length(value, schema, "maxLength", label, minimum=False)
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and re.search(pattern, value) is None:
            raise PlatformError("INVALID_REQUEST", f"{label} does not match the required pattern")

    if isinstance(value, dict):
        _validate_length(value, schema, "minProperties", label, minimum=True)
        _validate_length(value, schema, "maxProperties", label, minimum=False)
        required = schema.get("required", [])
        missing = [name for name in required if name not in value]
        if missing:
            raise PlatformError("INVALID_REQUEST", f"{label} is missing required fields: {', '.join(missing)}")
        properties = schema.get("properties", {})
        for name, child in properties.items():
            if name in value:
                validate_value(value[name], child, label=f"{label}.{name}")
        if schema.get("additionalProperties") is False:
            extras = set(value) - set(properties)
            if extras:
                raise PlatformError("INVALID_REQUEST", f"{label} has unknown fields: {', '.join(sorted(extras))}")

    if isinstance(value, list):
        _validate_length(value, schema, "minItems", label, minimum=True)
        _validate_length(value, schema, "maxItems", label, minimum=False)
        if isinstance(schema.get("items"), dict):
            for index, item in enumerate(value):
                validate_value(item, schema["items"], label=f"{label}[{index}]")


def _validate_bound_order(schema: dict[str, Any], lower: str, upper: str, label: str) -> None:
    if lower in schema and upper in schema and schema[lower] > schema[upper]:
        raise PlatformError("INVALID_REQUEST", f"{label}.{lower} must not exceed {upper}")


def _validate_numeric_bound(
    value: int | float,
    schema: dict[str, Any],
    keyword: str,
    label: str,
    *,
    inclusive: bool,
    lower: bool,
) -> None:
    if keyword not in schema:
        return
    bound = schema[keyword]
    if lower:
        valid = value >= bound if inclusive else value > bound
    else:
        valid = value <= bound if inclusive else value < bound
    if not valid:
        if lower:
            relation = "at least" if inclusive else "greater than"
        else:
            relation = "at most" if inclusive else "less than"
        raise PlatformError("INVALID_REQUEST", f"{label} must be {relation} {bound}")


def _validate_length(value: Any, schema: dict[str, Any], keyword: str, label: str, *, minimum: bool) -> None:
    if keyword not in schema:
        return
    bound = schema[keyword]
    valid = len(value) >= bound if minimum else len(value) <= bound
    if not valid:
        relation = "at least" if minimum else "at most"
        raise PlatformError("INVALID_REQUEST", f"{label} must contain {relation} {bound} items or characters")
