"""Provider-free P3 routing; neither type conveys execution authority."""


class P3PreparedSpawnMarker:
    __slots__ = ()


class P3SpawnError(RuntimeError):
    def __init__(self, reason: str, message: str):
        super().__init__(f'{reason}: {message}')
        self.reason = reason
