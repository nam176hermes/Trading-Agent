"""Closed schema and size authority for P1 Nautilus artifacts."""

from typing import Final


P1_ENGINE_CONFIGURATION_SCHEMA: Final = "nautilus-p1-engine-configuration-v1"
P1_INSTRUMENT_CATALOG_SCHEMA: Final = "nautilus-p1-instrument-catalog-v1"
P1_TARGET_SCHEDULE_SCHEMA: Final = "nautilus-p1-target-schedule-v1"
P1_MARKET_DATA_MANIFEST_SCHEMA: Final = "nautilus-p1-market-data-manifest-v1"

MAX_ENGINE_CONFIGURATION_BYTES: Final = 4_096
MAX_INSTRUMENT_CATALOG_BYTES: Final = 8_192
MAX_TARGET_SCHEDULE_BYTES: Final = 1_048_576
MAX_MARKET_DATA_MANIFEST_BYTES: Final = 8_192
