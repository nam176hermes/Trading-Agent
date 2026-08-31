from __future__ import annotations


def validated_target_prefix(
    target_ids: tuple[str, ...], accepted_target_prefix: tuple[str, ...] | None
) -> tuple[str, ...]:
    if accepted_target_prefix is None:
        return target_ids
    if (
        not accepted_target_prefix
        or accepted_target_prefix != target_ids[: len(accepted_target_prefix)]
    ):
        raise ValueError("paper target prefix is not sealed-schedule-bound")
    return accepted_target_prefix


__all__ = ["validated_target_prefix"]
