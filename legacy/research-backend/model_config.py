"""
model_config.py — Tiered LLM Strategy for Crypto Trading Agent

Routes different task types to appropriate LLM models:
- FLASH tier (deepseek-v4-flash): data summarization, JSON repair — cheap, fast
- PRO tier  (deepseek-v4-pro):   debates, decisions, reflection — reasoning-heavy

Models are configurable via environment variables:
  DEEPSEEK_FLASH_MODEL=deepseek-v4-flash   (default)
  DEEPSEEK_PRO_MODEL=deepseek-v4-pro       (default)

Cost strategy: use Flash wherever the model is summarizing existing data.
Use Pro for any task that requires original reasoning, debate, or decisions.
"""

import os
from dataclasses import dataclass, field
from enum import Enum


class TaskType(str, Enum):
    """Task type enum for model routing."""
    DATA_SUMMARIZATION = "data_summarization"
    ANALYST_REPORTS = "analyst_reports"
    BULL_BEAR_DEBATE = "bull_bear_debate"
    RISK_DEBATE = "risk_debate"
    TRADER_DECISION = "trader_decision"
    JSON_REPAIR = "json_repair"
    REFLECTION = "reflection"
    SYNTHESIS = "synthesis"
    DEFAULT = "default"


# ── Tier definitions ──────────────────────────────────────────
# FLASH: fast, cheap, good for summarizing existing data
FLASH_TASKS = {
    TaskType.DATA_SUMMARIZATION,
    TaskType.JSON_REPAIR,
}

# PRO: reasoning, debate, decisions — any task that generates original analysis
PRO_TASKS = {
    TaskType.ANALYST_REPORTS,
    TaskType.BULL_BEAR_DEBATE,
    TaskType.RISK_DEBATE,
    TaskType.TRADER_DECISION,
    TaskType.REFLECTION,
    TaskType.SYNTHESIS,
    TaskType.DEFAULT,
}


def _env_model(key: str, fallback: str) -> str:
    """Read model name from env var, falling back to default."""
    return os.getenv(key, fallback)


@dataclass
class ModelTier:
    """LLM model selection per task type.

    Each field maps to a TaskType value (lowercase attribute name).
    Models are resolved at access time so env var changes take effect.
    """

    _flash_model: str = field(default_factory=lambda: _env_model("DEEPSEEK_FLASH_MODEL", "deepseek-v4-flash"))
    _pro_model: str = field(default_factory=lambda: _env_model("DEEPSEEK_PRO_MODEL", "deepseek-v4-pro"))

    @property
    def data_summarization(self) -> str:
        return self._flash_model

    @property
    def analyst_reports(self) -> str:
        return self._pro_model

    @property
    def bull_bear_debate(self) -> str:
        return self._pro_model

    @property
    def risk_debate(self) -> str:
        return self._pro_model

    @property
    def trader_decision(self) -> str:
        return self._pro_model

    @property
    def json_repair(self) -> str:
        return self._flash_model

    @property
    def reflection(self) -> str:
        return self._pro_model

    @property
    def synthesis(self) -> str:
        return self._pro_model

    @property
    def default(self) -> str:
        return self._pro_model

    def describe(self) -> str:
        """Human-readable tier summary."""
        return (
            f"Flash: {self._flash_model} (data_summarization, json_repair)\n"
            f"Pro:   {self._pro_model} (analyst_reports, debate, decision, reflection, synthesis)"
        )


# Singleton instance
_model_config: ModelTier | None = None


def get_model_config() -> ModelTier:
    """Get the global model configuration singleton."""
    global _model_config
    if _model_config is None:
        _model_config = ModelTier()
    return _model_config


def set_model_config(config: ModelTier):
    """Override the global model configuration (for testing or custom configs)."""
    global _model_config
    _model_config = config
