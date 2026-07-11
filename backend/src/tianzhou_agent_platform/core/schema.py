from __future__ import annotations

from typing import Any

from tianzhou_agent_platform.core.errors import PlatformError

_JSON_TYPES = {"object", "array", "string", "number", "integer", "boolean", "null"}


def validate_schema(schema: dict[str, Any], *, label: str) -> None:
    """Validate the JSON Schema subset used by the MVP runtime.

    The runtime intentionally supports the portable structural subset needed
    for function calling: type, properties, required, items, enum, and
    additionalProperties. Unknown annotation keywords remain allowed.
    """

    if not isinstance(schema, dict):
        raise PlatformError("INVALID_REQUEST", f"{label} must be a JSON object")

    schema_type = schema.get("type")
    if schema_type is not None and schema_type not in _JSON_TYPES:
        raise PlatformError("INVALID_REQUEST", f"{label}.type is not supported: {schema_type!r}")

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
        validate_schema(schema["items"], label=f"{label}.items")


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

    if isinstance(value, dict):
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

    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, item in enumerate(value):
            validate_value(item, schema["items"], label=f"{label}[{index}]")
