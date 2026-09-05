"""Frozen ex-post daily participation calculation."""

from __future__ import annotations

from decimal import Decimal


class CapacityError(ValueError):
    """A required transition volume cannot support capacity evidence."""


def participation_samples(
    transitions: tuple[Decimal, ...],
    quote_volumes: tuple[Decimal, ...],
    *,
    nav: Decimal = Decimal("100000"),
) -> tuple[Decimal, ...]:
    if len(transitions) != len(quote_volumes):
        raise CapacityError("transition and volume samples must align")
    result = []
    for transition, volume in zip(transitions, quote_volumes, strict=True):
        if transition == 0:
            continue
        if volume <= 0:
            raise CapacityError("E_VOLUME: required quote volume is nonpositive")
        result.append(abs(transition) * nav / volume)
    return tuple(result)


__all__ = ["CapacityError", "participation_samples"]
