"""
scratchpad.py — Tool Call Audit Trail
Adapted from Dexter (virattt).

Logs every tool call as structured JSONL with:
  - timestamp, tool name, arguments
  - raw result + LLM summary
  - reasoning/thinking steps
  - cost tracking per step

Enables:
  - Full audit trail of every decision
  - Precise backtesting ("what data did the agent see?")
  - Richer reflection data ("the agent saw X but missed Y")

Scratchpad location: .dexter/scratchpad/{timestamp}_{hash}.jsonl
(Dexter naming convention preserved for compatibility)
"""

import json
import logging
import secrets
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from job_attribution import strict_worker_invocation
from local_artifacts import (
    UnsafeLocalArtifactError,
    bounded_directory_entries,
    exclusive_private_write,
    read_utf8_text,
)
from runtime_paths import data_root

log = logging.getLogger("scratchpad")

SCRATCHPAD_DIR = data_root() / ".dexter" / "scratchpad"
MAX_SESSION_FILE_BYTES = 4 * 1024 * 1024
MAX_SESSION_LINE_BYTES = 64 * 1024
MAX_SESSION_RECORDS = 10_000
MAX_SESSION_ENUM = 1_000
MAX_RECENT_SESSION_LIMIT = 100
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_STRICT_WORKER = strict_worker_invocation()
if _STRICT_WORKER is None:
    SCRATCHPAD_DIR.mkdir(parents=True, exist_ok=True)


class Scratchpad:
    """
    Dexter-style audit trail for a single research session.
    One instance per pipeline run = one .jsonl file.
    """

    def __init__(self, query: str = "", session_id: str = ""):
        self.query = query
        if session_id and not self._valid_session_id(session_id):
            raise ValueError("session_id must be 1-128 ASCII letters, digits, underscores, or hyphens")
        self.session_id = session_id or self._generate_session_id()
        self.steps: list[dict] = []
        self.start_time = datetime.now(timezone.utc)
        self.filepath = self._build_filepath()

    def _generate_session_id(self) -> str:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        return f"{ts}_{secrets.token_hex(16)}"

    @staticmethod
    def _valid_session_id(session_id: str) -> bool:
        return isinstance(session_id, str) and bool(_SESSION_ID_RE.fullmatch(session_id))

    def _build_filepath(self) -> Path:
        return SCRATCHPAD_DIR / f"{self.session_id}.jsonl"

    def init_session(self, query: str, symbols: list[str], mode: str = "snapshot"):
        """Log the start of a research session."""
        entry = {
            "type": "init",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "query": query,
            "symbols": symbols,
            "mode": mode,
            "session_id": self.session_id,
        }
        self.steps.append(entry)
        self.query = query
        return entry

    def log_tool_call(
        self,
        tool_name: str,
        args: dict[str, Any] | None = None,
        result: Any = None,
        llm_summary: str = "",
        success: bool = True,
        duration_ms: int = 0,
    ) -> dict:
        """
        Log a tool execution with full context.
        Matches Dexter's scratchpad schema for interop.
        """
        entry = {
            "type": "tool_result",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "toolName": tool_name,
            "args": args or {},
            "success": success,
            "duration_ms": duration_ms,
            "result": self._truncate_result(result),
            "llmSummary": llm_summary,
        }
        self.steps.append(entry)
        return entry

    def log_thinking(self, thought: str, category: str = "reasoning"):
        """Log an agent reasoning step."""
        entry = {
            "type": "thinking",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "category": category,  # "reasoning", "planning", "reflection", "validation"
            "content": thought,
        }
        self.steps.append(entry)
        return entry

    def log_llm_call(
        self,
        prompt: str,
        response: str,
        model: str = "unknown",
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> dict:
        """Log an LLM API call."""
        entry = {
            "type": "llm_call",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "prompt_preview": prompt[:200],
            "response_preview": response[:300],
        }
        self.steps.append(entry)
        return entry

    def log_validation(self, check_name: str, passed: bool, detail: str = ""):
        """Log a self-validation check."""
        entry = {
            "type": "validation",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "check": check_name,
            "passed": passed,
            "detail": detail,
        }
        self.steps.append(entry)
        return entry

    def log_final_decision(
        self,
        ticker: str,
        decision: str,
        confidence: float,
        reasoning: str,
        risk_level: str = "MEDIUM",
    ):
        """Log the final trading decision for a ticker."""
        entry = {
            "type": "final_decision",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "ticker": ticker,
            "decision": decision,
            "confidence": confidence,
            "risk_level": risk_level,
            "reasoning": reasoning[:500],
        }
        self.steps.append(entry)
        return entry

    def save(self) -> Path:
        """Write all steps to the JSONL file."""
        if len(self.steps) >= MAX_SESSION_RECORDS:
            raise ValueError("scratchpad exceeds the maximum record count")
        self.steps.append({
            "type": "session_end",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total_steps": len(self.steps),
            "duration_seconds": int(
                (datetime.now(timezone.utc) - self.start_time).total_seconds()
            ),
        })

        if _STRICT_WORKER is not None:
            log.info("[scratchpad] Strict worker audit remains in bounded process memory")
            return self.filepath

        encoder = json.JSONEncoder(default=str, ensure_ascii=False, allow_nan=False)
        encoded = bytearray()
        try:
            for step in self.steps:
                line_bytes = 0
                for fragment in encoder.iterencode(step):
                    line_remaining = MAX_SESSION_LINE_BYTES - line_bytes
                    file_remaining = MAX_SESSION_FILE_BYTES - len(encoded) - 1
                    if len(fragment) > line_remaining:
                        raise ValueError("scratchpad line exceeds the maximum audit size")
                    if len(fragment) > file_remaining:
                        raise ValueError("scratchpad exceeds the maximum audit size")
                    chunk = fragment.encode("utf-8", errors="strict")
                    if len(chunk) > line_remaining:
                        raise ValueError("scratchpad line exceeds the maximum audit size")
                    if len(chunk) > file_remaining:
                        raise ValueError("scratchpad exceeds the maximum audit size")
                    encoded.extend(chunk)
                    line_bytes += len(chunk)
                encoded.append(0x0A)
        except UnicodeEncodeError as exc:
            raise ValueError("scratchpad contains non-UTF-8 serializable data") from exc
        exclusive_private_write(self.filepath, bytes(encoded))

        log.info("[scratchpad] Session saved → %s (%d steps)", self.filepath, len(self.steps))
        return self.filepath

    def summary(self) -> dict:
        """Generate a quick summary of the session."""
        tool_calls = sum(1 for s in self.steps if s.get("type") == "tool_result")
        llm_calls = sum(1 for s in self.steps if s.get("type") == "llm_call")
        decisions = sum(1 for s in self.steps if s.get("type") == "final_decision")
        thinking_steps = sum(1 for s in self.steps if s.get("type") == "thinking")
        validations = sum(1 for s in self.steps if s.get("type") == "validation")
        errors = sum(1 for s in self.steps if s.get("type") == "tool_result" and not s.get("success"))

        return {
            "session_id": self.session_id,
            "steps": len(self.steps),
            "tool_calls": tool_calls,
            "llm_calls": llm_calls,
            "decisions": decisions,
            "thinking_steps": thinking_steps,
            "validations": validations,
            "errors": errors,
            "duration_seconds": int(
                (datetime.now(timezone.utc) - self.start_time).total_seconds()
            ),
        }

    @staticmethod
    def _truncate_result(result: Any, max_chars: int = 2000) -> Any:
        """Truncate large results to keep scratchpad manageable."""
        if result is None:
            return None
        if isinstance(result, str) and len(result) > max_chars:
            return result[:max_chars] + f"... [truncated, {len(result)} chars total]"
        if isinstance(result, dict):
            return {k: Scratchpad._truncate_result(v, max_chars // 5) for k, v in result.items()}
        if isinstance(result, list) and len(result) > 10:
            return result[:10] + [f"... [{len(result) - 10} more items]"]
        return result


def load_session(filepath: str) -> list[dict]:
    """Load a bounded, regular JSONL scratchpad session; reject malformed input."""
    try:
        data = read_utf8_text(filepath, max_bytes=MAX_SESSION_FILE_BYTES)
    except (OSError, UnsafeLocalArtifactError, TypeError, ValueError):
        return []

    steps: list[dict] = []
    for encoded_line in data.splitlines(keepends=True):
        if len(encoded_line.encode("utf-8", errors="strict")) > MAX_SESSION_LINE_BYTES:
            return []
        raw_line = encoded_line.rstrip("\r\n")
        if not raw_line.strip():
            continue
        if len(steps) >= MAX_SESSION_RECORDS:
            return []
        try:
            parsed = json.loads(
                raw_line,
                object_pairs_hook=_strict_json_object,
                parse_constant=lambda _value: (_ for _ in ()).throw(
                    ValueError("non-finite JSON constants are not accepted")
                ),
            )
        except (json.JSONDecodeError, RecursionError, ValueError, TypeError):
            return []
        if not isinstance(parsed, dict):
            return []
        steps.append(parsed)
    return steps


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if not isinstance(key, str) or key in result:
            raise ValueError("JSON object keys must be unique strings")
        result[key] = value
    return result


def list_recent_sessions(limit: int = 10) -> list[Path]:
    """List recent scratchpad sessions."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_RECENT_SESSION_LIMIT:
        return []
    files = bounded_directory_entries(
        SCRATCHPAD_DIR,
        suffix=".jsonl",
        max_entries=MAX_SESSION_ENUM,
    )
    dated: list[tuple[float, Path]] = []
    for path in files:
        try:
            dated.append((path.lstat().st_mtime, path))
        except OSError:
            continue
    dated.sort(reverse=True)
    return [path for _, path in dated[:limit]]


def replay_session(filepath: str) -> str:
    """
    Replay a session as a human-readable narrative.
    Useful for debugging: "What exactly did the agent do?"
    """
    steps = load_session(filepath)
    if not steps:
        return "No steps found."

    return render_session(Path(filepath).stem, steps)


def render_session(session_id: str, steps: list[dict]) -> str:
    """Render already-loaded replay events without reopening their source path."""
    if not steps:
        return "No steps found."

    lines = [f"# Session Replay: {session_id}"]
    lines.append(f"  Steps: {len(steps)}\n")

    for i, step in enumerate(steps):
        t = step.get("type", "unknown")
        ts = step.get("timestamp", "")[:19]

        if t == "init":
            lines.append(f"## Init — {ts}")
            lines.append(f"  Query: {step.get('query', 'N/A')}")
            lines.append(f"  Symbols: {step.get('symbols', [])}")
            lines.append(f"  Mode: {step.get('mode', 'unknown')}")

        elif t == "thinking":
            cat = step.get("category", "reasoning")
            lines.append(f"  💭 [{cat}] {step.get('content', '')[:200]}")

        elif t == "tool_result":
            name = step.get("toolName", "?")
            success = "✓" if step.get("success") else "✗"
            summary = step.get("llmSummary", "")
            lines.append(f"  🔧 {success} {name} — {summary[:150]}")

        elif t == "validation":
            check = step.get("check", "?")
            passed = "PASS" if step.get("passed") else "FAIL"
            lines.append(f"  ✅ {passed}: {check} — {step.get('detail', '')[:100]}")

        elif t == "final_decision":
            lines.append(f"## Decision — {step.get('ticker', '?')}")
            lines.append(f"  Signal: {step.get('decision', '?')}")
            lines.append(f"  Confidence: {step.get('confidence', '?')}")
            lines.append(f"  Risk: {step.get('risk_level', '?')}")
            lines.append(f"  Reasoning: {step.get('reasoning', '')[:300]}")

        elif t == "session_end":
            lines.append(f"\n## Session End")
            lines.append(f"  Duration: {step.get('duration_seconds', 0)}s")
            lines.append(f"  Total steps: {step.get('total_steps', 0)}")

    return "\n".join(lines)
