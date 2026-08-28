from __future__ import annotations

from datetime import timedelta

from packages.nautilus_runtime_contracts.semantic import semantic_digest

from test_events import stream


def test_semantic_digest_excludes_native_random_ids() -> None:
    events = stream()
    changed = tuple(
        event.model_copy(
            update={
                key: f"other-{key}"
                for key in ("native_order_id", "native_fill_id")
                if hasattr(event, key)
            }
        )
        for event in events
    )
    assert semantic_digest(events) == semantic_digest(changed)


def test_semantic_digest_changes_for_business_fields_and_event_order() -> None:
    events = stream()
    mutations = (
        events[:4] + (events[4].model_copy(update={"price": events[4].price + 1}),) + events[5:],
        events[:4] + (events[4].model_copy(update={"quantity": events[4].quantity + 1}),) + events[5:],
        events[:4] + (events[4].model_copy(update={"fee": events[4].fee + 1}),) + events[5:],
        events[:1] + (events[1].model_copy(update={"target_weight": events[1].target_weight / 2}),) + events[2:],
        events[:6] + (events[6].model_copy(update={"simulation_time": events[6].simulation_time + timedelta(minutes=1)}),) + events[7:],
        events[:6] + (events[7], events[6]) + events[8:],
    )
    for mutation in mutations:
        assert semantic_digest(events) != semantic_digest(mutation)
