"""RED source-custody and publication-custody controls."""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import fixture_root, source_io_subject


def test_source_leaf_swap_after_descriptor_binding_is_rejected(tmp_path, subject) -> None:
    """Break caught: a source leaf replaced after descriptor binding is accepted without a custody check."""
    root, _, surface = fixture_root(tmp_path, "Nautilus engine version: 1.227.0\n", name="nested/surface.md")
    io = source_io_subject(subject)
    swapped = False

    def replace_leaf(component: str) -> None:
        nonlocal swapped
        if not swapped and component == "surface.md":
            swapped = True
            surface.unlink()
            surface.write_text("Nautilus engine version: 1.231.0\n", encoding="utf-8")

    error = None
    try:
        io.snapshot(root, surface, replace_leaf)
    except ValueError as caught:
        error = caught
    assert swapped, "leaf-swap hook did not fire at descriptor binding"
    assert error is not None, "source bytes from a replaced leaf were accepted"


def test_source_parent_swap_after_descriptor_binding_is_rejected(tmp_path, subject) -> None:
    """Break caught: a checked parent directory can be replaced while its old descriptor remains readable."""
    root, _, surface = fixture_root(tmp_path, "Nautilus engine version: 1.227.0\n", name="nested/surface.md")
    io = source_io_subject(subject)
    swapped = False

    def replace_parent(component: str) -> None:
        nonlocal swapped
        if not swapped and component == "nested":
            swapped = True
            original_parent = root / "nested"
            original_parent.rename(root / "nested-before-swap")
            original_parent.mkdir()
            (original_parent / "surface.md").write_text("Nautilus engine version: 1.231.0\n", encoding="utf-8")

    error = None
    try:
        io.snapshot(root, surface, replace_parent)
    except ValueError as caught:
        error = caught
    assert swapped, "parent-swap hook did not fire at descriptor binding"
    assert error is not None, "source bytes from a replaced parent were accepted"


def test_inventory_target_swap_before_publication_preserves_concurrent_bytes(tmp_path, subject) -> None:
    """Break caught: publication overwrites a concurrently inode-replaced inventory target."""
    root, inventory, _ = fixture_root(tmp_path, "Nautilus engine version: 1.227.0\n")
    io = source_io_subject(subject)
    io.publish(root, inventory, lambda: None)
    original_inode = inventory.stat().st_ino
    swapped = False

    def replace_target_before_exchange() -> None:
        nonlocal swapped
        swapped = True
        concurrent = inventory.with_name("concurrent-inventory.json")
        concurrent.write_bytes(b"concurrent operator inventory\n")
        concurrent.replace(inventory)

    error = None
    try:
        io.publish(root, inventory, replace_target_before_exchange)
    except ValueError as caught:
        error = caught
    assert swapped, "pre-exchange hook did not fire"
    assert inventory.stat().st_ino != original_inode, "hook did not perform a real inode replacement"
    assert error is not None, "publication replaced concurrent target bytes"
    assert inventory.read_bytes() == b"concurrent operator inventory\n"
