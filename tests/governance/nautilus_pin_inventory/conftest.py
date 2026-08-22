"""Shared immutable identity oracle for pin-inventory unit controls."""

from __future__ import annotations

import importlib.util
from pathlib import Path


_path = Path(__file__).with_name("required_identities.py")
_spec = importlib.util.spec_from_file_location("p1_u00_required_identities", _path)
assert _spec and _spec.loader
required_identities = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(required_identities)
