"""Small fail-closed registry for canonical Arrow schema evolution."""

from __future__ import annotations

from packages.data_contracts import ArrowSchemaV1


class SchemaCompatibilityError(ValueError):
    """A schema reuses an identity or mutates an existing field."""


class SchemaRegistry:
    def __init__(self) -> None:
        self._schemas: dict[tuple[str, int], ArrowSchemaV1] = {}

    def register(self, schema: ArrowSchemaV1) -> str:
        value = ArrowSchemaV1.model_validate(schema)
        key = (value.schema_id, value.data_api_epoch)
        existing = self._schemas.get(key)
        if existing is not None:
            if existing.fingerprint != value.fingerprint:
                raise SchemaCompatibilityError("schema epoch already has different bytes")
            return existing.fingerprint

        previous = [
            item
            for (schema_id, _), item in self._schemas.items()
            if schema_id == value.schema_id
        ]
        if previous:
            latest = max(previous, key=lambda item: item.data_api_epoch)
            if value.data_api_epoch <= latest.data_api_epoch:
                raise SchemaCompatibilityError("schema epochs must increase")
            fields = {item.field_id: item for item in value.fields}
            for field in latest.fields:
                if fields.get(field.field_id) != field:
                    raise SchemaCompatibilityError(
                        "existing field identity and semantics are immutable"
                    )
            previous_ids = {field.field_id for field in latest.fields}
            if any(
                not field.nullable
                for field in value.fields
                if field.field_id not in previous_ids
            ):
                raise SchemaCompatibilityError("additive fields must be nullable")
        self._schemas[key] = value
        return value.fingerprint

    def require(self, schema_id: str, data_api_epoch: int) -> ArrowSchemaV1:
        try:
            return self._schemas[(schema_id, data_api_epoch)]
        except KeyError as exc:
            raise SchemaCompatibilityError("schema is not registered") from exc


__all__ = ["SchemaCompatibilityError", "SchemaRegistry"]
