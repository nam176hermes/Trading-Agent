"""Provider-free routing marker for an opaque prepared engine spawn."""


class EngineSpawnError(RuntimeError):
    """A closed reason code for an engine spawn authority refusal."""

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(f"{reason}: {message}")


class EnginePreparedSpawnMarker:
    """Carries no authority; the provider module enforces the exact token type."""

    __slots__ = ()


__all__ = ["EnginePreparedSpawnMarker", "EngineSpawnError"]
