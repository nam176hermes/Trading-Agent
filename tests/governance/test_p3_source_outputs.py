from packages.pre_p3_provenance import _excluded_output


def test_only_structurally_valid_exact_p3_outputs_can_be_excluded() -> None:
    assert not _excluded_output(
        "docs/implementation/p3/receipts/arbitrary.json","100644",b"{}"
    )
    assert not _excluded_output(
        "docs/implementation/p3/receipts/p3-phase-exit-v1.json","100644",b"{}"
    )
    assert not _excluded_output(
        "docs/implementation/p3/promotions/"+"a"*40+"-v1.json","100644",b"{}"
    )

