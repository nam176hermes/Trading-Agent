from pathlib import Path

from scripts.generate_p3_specs import generate_p3_specs


ROOT = Path(__file__).parents[2]


def test_p3_generation_is_byte_deterministic(tmp_path: Path) -> None:
    first = generate_p3_specs(tmp_path / "first")
    second = generate_p3_specs(tmp_path / "second")
    assert [path.name for path in first] == [path.name for path in second]
    assert [path.read_bytes() for path in first] == [path.read_bytes() for path in second]


def test_p3_source_lane_keeps_host_gates_separate() -> None:
    makefile = (ROOT / "Makefile").read_text()
    target = makefile.split("test-p3-source:", 1)[1].split("\n\n", 1)[0]
    assert "runtime-postgres" not in target
    assert "nautilus-native" not in target
    assert "tests/p3" in target
