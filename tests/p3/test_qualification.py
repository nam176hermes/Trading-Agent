from packages.alpha_lifecycle import protocol
from packages.alpha_lifecycle import qualification


def test_adapter_uses_the_unchanged_frozen_qualification_function() -> None:
    assert qualification.evaluate_alpha_qualification is protocol.evaluate_alpha_qualification
