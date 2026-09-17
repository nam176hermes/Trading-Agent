import os
import sys

import pytest

from scripts import record_p3_promotion as producer


def test_invalid_promotion_does_not_create_output(tmp_path, monkeypatch):
    output = tmp_path / "receipt.json"
    monkeypatch.setattr(sys, "argv", ["record_p3_promotion", "--output", str(output)])

    def invalid(_environment):
        raise ValueError("invalid authority")

    monkeypatch.setattr(producer, "build_promotion", invalid)
    with pytest.raises(ValueError, match="invalid authority"):
        producer.main()
    assert not output.exists()


@pytest.mark.parametrize("fail", [False, True])
def test_short_writes_are_completed_or_owned_output_removed(tmp_path, monkeypatch, fail):
    output = tmp_path / "receipt.json"
    monkeypatch.setattr(sys, "argv", ["record_p3_promotion", "--output", str(output)])
    monkeypatch.setattr(producer, "build_promotion", lambda _: {"test": "synthetic"})
    write = os.write
    calls = 0

    def short_write(descriptor, data):
        nonlocal calls
        calls += 1
        if fail and calls == 2:
            raise OSError("injected write failure")
        return write(descriptor, data[:3])

    monkeypatch.setattr(producer.os, "write", short_write)
    if fail:
        with pytest.raises(OSError, match="injected"):
            producer.main()
        assert not output.exists()
    else:
        producer.main()
        assert output.read_bytes() == b'{"test":"synthetic"}\n'
        assert calls > 1
        with pytest.raises(FileExistsError):
            producer.main()


@pytest.mark.parametrize("failure", ["stale", "malformed", "authority"])
@pytest.mark.parametrize("existing", [False, True])
def test_skip_stale_only_skips_valid_history_without_reusing_output(tmp_path, monkeypatch, failure, existing):
    output = tmp_path / "receipt.json"
    monkeypatch.setattr(sys, "argv", ["record_p3_promotion", "--output", str(output), "--skip-stale"])
    if existing:
        output.write_bytes(b"preserve")

    def build(_environment):
        if failure == "stale":
            raise producer.StalePhaseExitError("old closure")
        raise ValueError(failure)

    monkeypatch.setattr(producer, "build_promotion", build)
    if failure == "stale" and not existing:
        producer.main()
        assert not output.exists()
    else:
        with pytest.raises(FileExistsError if failure == "stale" else ValueError):
            producer.main()
    if existing:
        assert output.read_bytes() == b"preserve"
