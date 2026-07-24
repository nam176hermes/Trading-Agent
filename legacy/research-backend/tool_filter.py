"""
tool_filter.py — Scoped tool catalog for LLM calls.

Inspired by FinceptTerminal's ToolFilter: reduces prompt noise by only
sending relevant tool definitions to the LLM. Three layers:

1. infer_toolsets(task) — keyword-based auto-selection (which toolsets needed?)
2. ToolFilter — category filter, name regex, max_tools cap
3. estimate_savings() — how many tokens saved by filtering

Integrates with cron job enabled_toolsets for automatic tool scoping.
"""

import re
from typing import List, Optional, Set


# ── Toolset Definitions ────────────────────────────────────────

# Maps toolset name → {description, keywords, tools}
# Keywords are matched against task descriptions for auto-inference
TOOLSETS = {
    "web": {
        "description": "Web search and content extraction",
        "keywords": ["search", "web", "google", "find", "lookup", "research",
                     "scrape", "extract", "fetch url", "browse", "navigate"],
        "tools": ["web_search", "web_search_plus", "web_extract", "web_extract_plus"],
    },
    "terminal": {
        "description": "Shell commands, git, builds, package management",
        "keywords": ["run", "execute", "build", "compile", "install", "git",
                     "bash", "shell", "command", "script", "python", "npm", "pip",
                     "docker", "test", "commit", "push", "deploy", "clone",
                     "repo", "github", "package", "dependency"],
        "tools": ["terminal", "process", "execute_code"],
    },
    "file": {
        "description": "File read/write/update operations",
        "keywords": ["file", "read", "write", "save", "edit", "patch",
                     "create file", "modify", "config", "json", "yaml", "csv",
                     "code", "source", "script", ".py", ".js", ".ts", ".md"],
        "tools": ["read_file", "write_file", "patch", "search_files"],
    },
    "browser": {
        "description": "Browser automation and web interaction",
        "keywords": ["browser", "click", "screenshot", "form", "login",
                     "interact", "javascript", "spa", "dynamic page"],
        "tools": ["browser_navigate", "browser_click", "browser_snapshot",
                  "browser_type", "browser_scroll", "browser_console",
                  "browser_vision", "browser_press"],
    },
    "search": {
        "description": "Search-only (no extraction)",
        "keywords": [],  # Subsumed by web; use when only searching needed
        "tools": ["web_search", "web_search_plus"],
    },
    "skills": {
        "description": "Skill loading and management",
        "keywords": ["skill", "workflow", "procedure", "template",
                     "how to", "approach", "pattern"],
        "tools": ["skill_view", "skills_list", "skill_manage"],
    },
    "session_search": {
        "description": "Search past conversation sessions",
        "keywords": ["remember", "recall", "previous", "last time",
                     "history", "past session", "what did we"],
        "tools": ["session_search", "memory"],
    },
    "cronjob": {
        "description": "Cron job scheduling",
        "keywords": ["schedule", "cron", "every day", "hourly",
                     "recurring", "repeat", "periodic"],
        "tools": ["cronjob"],
    },
    "delegation": {
        "description": "Parallel subagent delegation",
        "keywords": ["parallel", "delegate", "subagent", "multi-agent",
                     "spawn", "worker", "distribute"],
        "tools": ["delegate_task"],
    },
    "vision": {
        "description": "Image analysis and screenshots",
        "keywords": ["image", "picture", "photo", "screenshot",
                     "see", "look at", "visual", "chart image"],
        "tools": ["vision_analyze"],
    },
    "todo": {
        "description": "Task list tracking",
        "keywords": [],  # Always useful for complex tasks
        "tools": ["todo"],
    },
    "fact_store": {
        "description": "Deep structured memory (entity resolution)",
        "keywords": ["fact", "entity", "relationship", "structured memory",
                     "long-term", "knowledge graph"],
        "tools": ["fact_store", "fact_feedback"],
    },
}

# ── Keyword → Toolset Inference ────────────────────────────────

def infer_toolsets(task_description: str, min_confidence: int = 1) -> List[str]:
    """
    Auto-detect which toolsets a task needs based on keyword matching.

    Args:
        task_description: Natural language description of the task
        min_confidence: Minimum keyword matches required (default: 1)

    Returns:
        List of toolset names sorted by relevance (most matches first)

    Example:
        >>> infer_toolsets("Search GitHub for Python repos and clone the best one")
        ['web', 'terminal', 'file']
    """
    desc_lower = task_description.lower()
    scored = []

    for name, meta in TOOLSETS.items():
        if not meta["keywords"]:
            continue
        matches = sum(1 for kw in meta["keywords"] if kw in desc_lower)
        if matches >= min_confidence:
            scored.append((matches, name))

    # Sort by match count descending
    scored.sort(reverse=True)
    result = [name for _, name in scored]

    # Always include 'todo' for complex tasks (3+ words)
    if len(task_description.split()) > 3 and "todo" not in result:
        result.append("todo")

    return result


# ── ToolFilter Class ───────────────────────────────────────────

class ToolFilter:
    """
    Filter tool catalogs by category, name pattern, or count cap.

    Inspired by FinceptTerminal's McpService::get_all_tools(ToolFilter).
    """

    def __init__(
        self,
        categories: Optional[Set[str]] = None,
        exclude_categories: Optional[Set[str]] = None,
        name_patterns: Optional[List[str]] = None,
        exclude_name_patterns: Optional[List[str]] = None,
        max_tools: Optional[int] = None,
    ):
        """
        Args:
            categories: Only include tools in these categories
            exclude_categories: Exclude tools in these categories
            name_patterns: Only include tools matching these regex patterns
            exclude_name_patterns: Exclude tools matching these regex patterns
            max_tools: Cap total tools returned (keep highest-priority first)
        """
        self.categories = categories or set()
        self.exclude_categories = exclude_categories or set()
        self.name_patterns = [re.compile(p) for p in (name_patterns or [])]
        self.exclude_name_patterns = [re.compile(p) for p in (exclude_name_patterns or [])]
        self.max_tools = max_tools

    def apply(self, tools: List[dict]) -> List[dict]:
        """
        Filter a list of tool definitions.

        Each tool dict should have: name (str), category (str, optional)

        Returns filtered list.
        """
        result = []

        for tool in tools:
            name = tool.get("name", "")
            category = tool.get("category", "")

            # Category filter
            if self.categories and category not in self.categories:
                continue
            if category in self.exclude_categories:
                continue

            # Name pattern filter
            if self.name_patterns:
                if not any(p.search(name) for p in self.name_patterns):
                    continue
            if self.exclude_name_patterns:
                if any(p.search(name) for p in self.exclude_name_patterns):
                    continue

            result.append(tool)

        # Cap
        if self.max_tools and len(result) > self.max_tools:
            result = result[:self.max_tools]

        return result

    def apply_to_names(self, tool_names: List[str], categories: Optional[dict] = None) -> List[str]:
        """
        Filter a list of tool names (with optional category lookup).

        Args:
            tool_names: List of tool name strings
            categories: Optional dict mapping tool_name → category
        """
        result = []

        for name in tool_names:
            category = categories.get(name, "") if categories else ""

            if self.categories and category not in self.categories:
                continue
            if category in self.exclude_categories:
                continue

            if self.name_patterns:
                if not any(p.search(name) for p in self.name_patterns):
                    continue
            if self.exclude_name_patterns:
                if any(p.search(name) for p in self.exclude_name_patterns):
                    continue

            result.append(name)

        if self.max_tools and len(result) > self.max_tools:
            result = result[:self.max_tools]

        return result


# ── Token Savings Estimator ────────────────────────────────────

def estimate_savings(
    total_tools: int,
    filtered_tools: int,
    avg_tool_tokens: int = 800,
) -> dict:
    """
    Estimate token savings from tool filtering.

    Args:
        total_tools: Total tools before filtering
        filtered_tools: Tools after filtering
        avg_tool_tokens: Average tokens per tool definition (default 800)

    Returns:
        Dict with savings metrics
    """
    removed = total_tools - filtered_tools
    tokens_saved = removed * avg_tool_tokens
    pct = (removed / total_tools * 100) if total_tools > 0 else 0

    return {
        "total_tools": total_tools,
        "filtered_tools": filtered_tools,
        "removed": removed,
        "tokens_saved": tokens_saved,
        "pct_saved": round(pct, 1),
        "prompt_reduction": f"{pct:.0f}% ({tokens_saved:,} tokens saved per LLM call)",
    }


def auto_cron_toolsets(task_description: str) -> List[str]:
    """
    Auto-generate the enabled_toolsets list for a cron job.

    Use this when creating cron jobs to automatically scope tools.
    Always includes 'terminal' (needed for execution) unless the
    task is purely read-only web search.

    Example:
        >>> auto_cron_toolsets("Fetch BTC price and post to Telegram")
        ['web', 'terminal']
    """
    inferred = infer_toolsets(task_description)

    # Always include terminal for cron (needed for any script execution)
    if "terminal" not in inferred:
        inferred.append("terminal")

    # Remove toolsets that don't make sense in cron context
    # (browser, vision — cron is headless; delegation — cron IS the agent)
    exclude_cron = {"browser", "vision", "delegation", "cronjob"}
    result = [t for t in inferred if t not in exclude_cron]

    return result


# ── CLI ────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python tool_filter.py '<task description>'")
        print("       python tool_filter.py --list")
        sys.exit(1)

    if sys.argv[1] == "--list":
        print("Available toolsets:\n")
        for name, meta in sorted(TOOLSETS.items()):
            print(f"  {name:20s} — {meta['description']}")
            print(f"  {'':20s}   tools: {', '.join(meta['tools'][:5])}" +
                  (f" +{len(meta['tools'])-5} more" if len(meta['tools']) > 5 else ""))
            print(f"  {'':20s}   keywords: {', '.join(meta['keywords'][:5])}" +
                  (f" +{len(meta['keywords'])-5} more" if len(meta['keywords']) > 5 else ""))
            print()
    else:
        task = " ".join(sys.argv[1:])
        toolsets = infer_toolsets(task)
        print(f"Task: {task}")
        print(f"Inferred toolsets: {toolsets}")
        print(f"Est. savings: {estimate_savings(43, 10 + len(toolsets)*3)['prompt_reduction']}")
