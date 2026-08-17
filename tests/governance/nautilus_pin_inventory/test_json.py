"""RED JSON parser controls."""

from __future__ import annotations

from conftest import fixture_root, generate_baseline, run_subject


def test_duplicate_json_object_member_is_rejected_before_flattening(tmp_path, subject) -> None:
    """Break caught: duplicate governed JSON keys collapse to one inventory citation."""
    root, inventory, surface = fixture_root(
        tmp_path,
        '{"engine_version":"1.227.0","upstream_commit":"280ae1762df51a492a4ce71506a40b5c8706def5","profile_manifest_schema_version":6}',
        name="engines/nautilus/engine-build-policy.json",
    )
    generate_baseline(subject, root, inventory)
    surface.write_text(
        '{"engine_version":"1.227.0","engine_version":"1.227.0","upstream_commit":"280ae1762df51a492a4ce71506a40b5c8706def5","profile_manifest_schema_version":6}',
        encoding="utf-8",
    )
    result = run_subject(subject, root, inventory)
    assert result.returncode == 2, f"duplicate member was not rejected: {result.stdout}\n{result.stderr}"
