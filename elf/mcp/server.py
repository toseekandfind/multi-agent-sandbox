#!/usr/bin/env python3
"""
ELF MCP Server - Emergent Learning Framework exposed via Model Context Protocol.

Adapted for multi-agent-sandbox with client-scoped databases.

This server exposes ELF's knowledge and recording capabilities to Claude Code agents,
enabling them to:
- Query for context (golden rules, heuristics, learnings)
- Record heuristics and failures in real-time
- Search knowledge base
- Record plans and postmortems

Usage:
    # Start server for a specific client
    CLIENT_ID=client-a python -m elf.mcp.server

    # Add to Claude Code
    claude mcp add elf -- env CLIENT_ID=client-a python -m elf.mcp.server
"""

import json
import os
import re
import sys
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum

# Add parent directories to path for imports
SCRIPT_DIR = Path(__file__).resolve().parent
ELF_DIR = SCRIPT_DIR.parent
BASE_DIR = ELF_DIR.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# MCP imports
try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    print("Error: mcp package not installed. Run: pip install mcp", file=sys.stderr)
    sys.exit(1)

from pydantic import BaseModel, Field, field_validator, ConfigDict

# Import our ELFMemory
from elf.memory import ELFMemory

# Get client ID from environment (default for local dev)
CLIENT_ID = os.getenv("CLIENT_ID", "default")

# Initialize the MCP server
mcp = FastMCP(f"ELF-{CLIENT_ID}")


def get_client_elf_db() -> str:
    """Get the ELF database path for the current client."""
    # Check if ELF_DB_PATH is explicitly set
    if os.getenv("ELF_DB_PATH"):
        return os.getenv("ELF_DB_PATH")
    # Otherwise use client-scoped path
    base = Path.home() / ".claude" / "elf" / CLIENT_ID
    base.mkdir(parents=True, exist_ok=True)
    return str(base / "memory.db")


# Lazy-loaded memory instance
_memory: Optional[ELFMemory] = None


def get_memory() -> ELFMemory:
    """Get or create ELFMemory instance."""
    global _memory
    if _memory is None:
        db_path = get_client_elf_db()
        _memory = ELFMemory(db_path=db_path)
        # Ensure plan/postmortem tables exist
        _init_plan_tables(_memory)
    return _memory


def _init_plan_tables(memory: ELFMemory):
    """Initialize plan and postmortem tables if they don't exist."""
    with memory._get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                approach TEXT,
                risks TEXT,
                expected_outcome TEXT,
                domain TEXT,
                status TEXT DEFAULT 'active',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                completed_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS postmortems (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plan_id INTEGER,
                title TEXT NOT NULL,
                actual_outcome TEXT NOT NULL,
                divergences TEXT,
                went_well TEXT,
                went_wrong TEXT,
                lessons TEXT,
                domain TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (plan_id) REFERENCES plans (id)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_plans_status ON plans(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_postmortems_plan ON postmortems(plan_id)")
        conn.commit()


# ============================================================================
# Pydantic Models for Input Validation
# ============================================================================

class DepthLevel(str, Enum):
    """Context depth levels for querying."""
    MINIMAL = "minimal"
    STANDARD = "standard"
    DEEP = "deep"


class SourceType(str, Enum):
    """Source types for heuristics."""
    FAILURE = "failure"
    SUCCESS = "success"
    OBSERVATION = "observation"


class QueryInput(BaseModel):
    """Input for elf_query tool."""
    model_config = ConfigDict(str_strip_whitespace=True)

    domain: Optional[str] = Field(
        default=None,
        description="Domain to focus on (e.g., 'authentication', 'database')",
        max_length=100
    )
    project_path: Optional[str] = Field(
        default=None,
        description="Project path to filter context",
        max_length=500
    )
    depth: DepthLevel = Field(
        default=DepthLevel.STANDARD,
        description="Context depth: 'minimal' (golden rules only), 'standard' (+ heuristics), 'deep' (+ recent outcomes)"
    )


class RecordHeuristicInput(BaseModel):
    """Input for elf_record_heuristic tool."""
    model_config = ConfigDict(str_strip_whitespace=True)

    domain: str = Field(
        ...,
        description="Domain for the heuristic (e.g., 'react', 'testing', 'git')",
        min_length=1,
        max_length=100
    )
    rule: str = Field(
        ...,
        description="The heuristic rule statement",
        min_length=5,
        max_length=500
    )
    explanation: str = Field(
        default="",
        description="Explanation of why this heuristic works",
        max_length=2000
    )
    source: SourceType = Field(
        default=SourceType.OBSERVATION,
        description="How this heuristic was discovered"
    )
    confidence: float = Field(
        default=0.7,
        description="Confidence level from 0.0 to 1.0",
        ge=0.0,
        le=1.0
    )
    project_path: Optional[str] = Field(
        default=None,
        description="Project this heuristic applies to (None = global)",
        max_length=500
    )

    @field_validator('domain')
    @classmethod
    def validate_domain(cls, v: str) -> str:
        """Sanitize domain."""
        v = v.lower().replace(' ', '-')
        v = re.sub(r'[^a-z0-9-]', '', v)
        return v.strip('-')


class RecordOutcomeInput(BaseModel):
    """Input for elf_record_outcome tool."""
    model_config = ConfigDict(str_strip_whitespace=True)

    job_id: str = Field(
        ...,
        description="Job identifier",
        min_length=1,
        max_length=100
    )
    job_type: str = Field(
        ...,
        description="Type of job (e.g., 'agent_farm', 'claude_chat')",
        min_length=1,
        max_length=50
    )
    outcome: str = Field(
        ...,
        description="Outcome: 'success', 'failure', or 'partial'",
        pattern="^(success|failure|partial)$"
    )
    project_path: Optional[str] = Field(
        default=None,
        description="Project path"
    )
    duration_seconds: Optional[float] = Field(
        default=None,
        description="Duration in seconds"
    )
    files_touched: Optional[List[str]] = Field(
        default=None,
        description="List of files modified"
    )
    learnings: Optional[List[str]] = Field(
        default=None,
        description="Lessons learned"
    )
    error_message: Optional[str] = Field(
        default=None,
        description="Error message if failed"
    )


class SearchInput(BaseModel):
    """Input for elf_search tool."""
    model_config = ConfigDict(str_strip_whitespace=True)

    query: str = Field(
        ...,
        description="Search query",
        min_length=2,
        max_length=500
    )
    domain: Optional[str] = Field(
        default=None,
        description="Domain to filter results"
    )
    limit: int = Field(
        default=10,
        description="Maximum results",
        ge=1,
        le=50
    )


class RecordPlanInput(BaseModel):
    """Input for elf_record_plan tool."""
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(..., description="Brief task title", min_length=3, max_length=200)
    description: str = Field(default="", description="What we're trying to accomplish", max_length=2000)
    approach: str = Field(default="", description="How we plan to do it", max_length=2000)
    risks: str = Field(default="", description="Identified risks/concerns", max_length=1000)
    expected_outcome: str = Field(default="", description="What success looks like", max_length=1000)
    domain: str = Field(default="", description="Domain category", max_length=100)


class RecordPostmortemInput(BaseModel):
    """Input for elf_record_postmortem tool."""
    model_config = ConfigDict(str_strip_whitespace=True)

    plan_id: Optional[int] = Field(default=None, description="Link to plan ID (recommended)")
    title: str = Field(default="", description="Brief description", max_length=200)
    actual_outcome: str = Field(..., description="What actually happened", min_length=5, max_length=2000)
    divergences: str = Field(default="", description="What differed from plan", max_length=2000)
    went_well: str = Field(default="", description="What succeeded", max_length=1000)
    went_wrong: str = Field(default="", description="What failed", max_length=1000)
    lessons: str = Field(default="", description="Key takeaways", max_length=2000)
    domain: str = Field(default="", description="Domain category", max_length=100)


# ============================================================================
# MCP Tools
# ============================================================================

@mcp.tool(name="elf_query")
def elf_query(params: QueryInput) -> str:
    """
    Query the Emergent Learning Framework for context.

    Returns golden rules, heuristics, and learnings relevant to the current task.
    This is the primary way to load institutional knowledge before starting work.

    Returns formatted context with:
    - Golden rules (always included)
    - Domain-specific heuristics
    - Recent outcomes and learnings (deep mode)
    """
    try:
        memory = get_memory()

        context = memory.get_context(
            project_path=params.project_path,
            domain=params.domain
        )

        # Format based on depth
        result = {
            "client_id": CLIENT_ID,
            "depth": params.depth.value,
            "golden_rules": context.get("golden_rules", []),
        }

        if params.depth in [DepthLevel.STANDARD, DepthLevel.DEEP]:
            result["heuristics"] = context.get("heuristics", [])

        if params.depth == DepthLevel.DEEP:
            result["recent_outcomes"] = context.get("recent_outcomes", [])
            result["trails"] = context.get("trails", [])

        result["prompt_context"] = context.get("prompt_context", "")

        return json.dumps(result, indent=2, default=str)

    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {str(e)}"})


@mcp.tool(name="elf_record_heuristic")
def elf_record_heuristic(params: RecordHeuristicInput) -> str:
    """
    Record a new heuristic in ELF.

    Heuristics are reusable rules of thumb discovered through work.
    They should be actionable, specific, and testable.
    """
    try:
        memory = get_memory()

        heuristic_id = memory.add_heuristic(
            domain=params.domain,
            rule=params.rule,
            explanation=params.explanation,
            project_path=params.project_path,
            confidence=params.confidence,
            source_type=params.source.value
        )

        return json.dumps({
            "success": True,
            "heuristic_id": heuristic_id,
            "client_id": CLIENT_ID,
            "domain": params.domain,
            "rule": params.rule,
            "confidence": params.confidence
        }, indent=2)

    except Exception as e:
        return json.dumps({"success": False, "error": f"{type(e).__name__}: {str(e)}"})


@mcp.tool(name="elf_record_outcome")
def elf_record_outcome(params: RecordOutcomeInput) -> str:
    """
    Record a job outcome in ELF.

    Outcomes are used to track success/failure patterns and build
    the knowledge base over time.
    """
    try:
        memory = get_memory()

        outcome_id = memory.record_outcome(
            job_id=params.job_id,
            job_type=params.job_type,
            outcome=params.outcome,
            project_path=params.project_path,
            duration_seconds=params.duration_seconds,
            files_touched=params.files_touched,
            learnings=params.learnings,
            error_message=params.error_message
        )

        return json.dumps({
            "success": True,
            "outcome_id": outcome_id,
            "client_id": CLIENT_ID,
            "job_id": params.job_id,
            "outcome": params.outcome
        }, indent=2)

    except Exception as e:
        return json.dumps({"success": False, "error": f"{type(e).__name__}: {str(e)}"})


@mcp.tool(name="elf_search")
def elf_search(params: SearchInput) -> str:
    """
    Search the ELF knowledge base for relevant information.

    Searches across heuristics and learnings to find knowledge
    relevant to a query.
    """
    try:
        memory = get_memory()

        # Get heuristics matching domain
        heuristics = memory.get_heuristics(
            domain=params.domain,
            limit=params.limit
        )

        # Filter by query keywords
        keywords = params.query.lower().split()
        matching_heuristics = []
        for h in heuristics:
            rule_lower = h.get("rule", "").lower()
            expl_lower = (h.get("explanation") or "").lower()
            if any(kw in rule_lower or kw in expl_lower for kw in keywords):
                matching_heuristics.append(h)

        # Get golden rules
        golden_rules = memory.get_golden_rules()
        matching_golden = []
        for r in golden_rules:
            rule_lower = r.get("rule", "").lower()
            if any(kw in rule_lower for kw in keywords):
                matching_golden.append(r)

        return json.dumps({
            "client_id": CLIENT_ID,
            "query": params.query,
            "domain": params.domain,
            "golden_rules": matching_golden[:5],
            "heuristics": matching_heuristics[:params.limit],
            "total_results": len(matching_golden) + len(matching_heuristics)
        }, indent=2, default=str)

    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {str(e)}"})


@mcp.tool(name="elf_stats")
def elf_stats() -> str:
    """
    Get ELF memory statistics for the current client.

    Returns counts of heuristics, golden rules, outcomes, etc.
    """
    try:
        memory = get_memory()
        stats = memory.get_stats()
        stats["client_id"] = CLIENT_ID
        return json.dumps(stats, indent=2)

    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {str(e)}"})


@mcp.tool(name="elf_validate_heuristic")
def elf_validate_heuristic(heuristic_id: int) -> str:
    """
    Mark a heuristic as validated (increases confidence).

    Call this when you verify a heuristic works as expected.
    """
    try:
        memory = get_memory()
        memory.validate_heuristic(heuristic_id)

        return json.dumps({
            "success": True,
            "heuristic_id": heuristic_id,
            "action": "validated",
            "message": "Confidence increased"
        }, indent=2)

    except Exception as e:
        return json.dumps({"success": False, "error": f"{type(e).__name__}: {str(e)}"})


@mcp.tool(name="elf_violate_heuristic")
def elf_violate_heuristic(heuristic_id: int) -> str:
    """
    Mark a heuristic as violated (decreases confidence).

    Call this when a heuristic doesn't work as expected.
    """
    try:
        memory = get_memory()
        memory.violate_heuristic(heuristic_id)

        return json.dumps({
            "success": True,
            "heuristic_id": heuristic_id,
            "action": "violated",
            "message": "Confidence decreased"
        }, indent=2)

    except Exception as e:
        return json.dumps({"success": False, "error": f"{type(e).__name__}: {str(e)}"})


def _generate_task_id(title: str) -> str:
    """Generate a unique task_id slug from title."""
    slug = title.lower()
    slug = re.sub(r'[^a-z0-9\s-]', '', slug)
    slug = re.sub(r'\s+', '-', slug)
    slug = slug[:40].strip('-')
    timestamp = datetime.now().strftime('%Y%m%d-%H%M')
    return f"{slug}-{timestamp}"


@mcp.tool(name="elf_record_plan")
def elf_record_plan(params: RecordPlanInput) -> str:
    """
    Record a plan before starting a task.

    Part of the plan-postmortem learning system that enables higher-quality
    learning by comparing intent vs. outcome.

    Workflow:
      1. Record plan (this tool)
      2. Execute the work
      3. Record postmortem (elf_record_postmortem) with plan_id
      4. System compares expected vs actual for insights
    """
    try:
        memory = get_memory()
        task_id = _generate_task_id(params.title)

        with memory._get_conn() as conn:
            cursor = conn.execute("""
                INSERT INTO plans (task_id, title, description, approach, risks, expected_outcome, domain)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (task_id, params.title, params.description, params.approach,
                  params.risks, params.expected_outcome, params.domain))
            plan_id = cursor.lastrowid
            conn.commit()

        return json.dumps({
            "success": True,
            "plan_id": plan_id,
            "task_id": task_id,
            "client_id": CLIENT_ID,
            "title": params.title,
            "message": f"Plan recorded. Use plan_id={plan_id} when creating postmortem."
        }, indent=2)

    except Exception as e:
        return json.dumps({"success": False, "error": f"{type(e).__name__}: {str(e)}"})


@mcp.tool(name="elf_record_postmortem")
def elf_record_postmortem(params: RecordPostmortemInput) -> str:
    """
    Record a postmortem after completing a task.

    When linked to a plan, enables higher-quality learning by
    comparing expected vs. actual outcomes.
    """
    try:
        memory = get_memory()

        with memory._get_conn() as conn:
            conn.row_factory = lambda c, r: dict(zip([col[0] for col in c.description], r))

            # Get plan info if linked
            plan = None
            if params.plan_id:
                cursor = conn.execute("SELECT * FROM plans WHERE id = ?", (params.plan_id,))
                plan = cursor.fetchone()
                if not plan:
                    return json.dumps({"success": False, "error": f"Plan ID {params.plan_id} not found"})

            # Determine title
            title = params.title
            if not title and plan:
                title = f"Postmortem: {plan['title']}"
            if not title:
                title = "Untitled Postmortem"

            # Determine domain
            domain = params.domain or (plan.get('domain', '') if plan else '')

            # Insert postmortem
            cursor = conn.execute("""
                INSERT INTO postmortems (plan_id, title, actual_outcome, divergences, went_well, went_wrong, lessons, domain)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (params.plan_id, title, params.actual_outcome, params.divergences,
                  params.went_well, params.went_wrong, params.lessons, domain))
            postmortem_id = cursor.lastrowid

            # Mark plan as completed if linked
            if params.plan_id:
                conn.execute("""
                    UPDATE plans SET status = 'completed', completed_at = ? WHERE id = ?
                """, (datetime.now().isoformat(), params.plan_id))

            conn.commit()

        result = {
            "success": True,
            "postmortem_id": postmortem_id,
            "plan_id": params.plan_id,
            "client_id": CLIENT_ID,
            "linked_to_plan": params.plan_id is not None,
            "title": title
        }

        # Add learning analysis if linked to plan
        if plan:
            result["analysis"] = {
                "plan_title": plan["title"],
                "expected_outcome": plan.get("expected_outcome", ""),
                "actual_outcome": params.actual_outcome,
                "had_divergences": bool(params.divergences),
                "lessons_captured": bool(params.lessons)
            }

            # Auto-create heuristic if lessons captured
            if params.lessons and domain:
                try:
                    memory.add_heuristic(
                        domain=domain,
                        rule=params.lessons[:200],
                        explanation=f"Learned from postmortem: {title}",
                        confidence=0.6,
                        source_type="observation"
                    )
                    result["heuristic_created"] = True
                except Exception:
                    pass

        return json.dumps(result, indent=2)

    except Exception as e:
        return json.dumps({"success": False, "error": f"{type(e).__name__}: {str(e)}"})


@mcp.tool(name="elf_list_plans")
def elf_list_plans(status: str = "active", limit: int = 10) -> str:
    """
    List plans by status.

    Args:
        status: Filter by status ('active', 'completed', 'all')
        limit: Maximum number of plans to return
    """
    try:
        memory = get_memory()

        with memory._get_conn() as conn:
            if status == "all":
                cursor = conn.execute("""
                    SELECT * FROM plans ORDER BY created_at DESC LIMIT ?
                """, (limit,))
            else:
                cursor = conn.execute("""
                    SELECT * FROM plans WHERE status = ? ORDER BY created_at DESC LIMIT ?
                """, (status, limit))

            plans = [dict(row) for row in cursor.fetchall()]

        return json.dumps({
            "client_id": CLIENT_ID,
            "status_filter": status,
            "plans": plans,
            "count": len(plans)
        }, indent=2, default=str)

    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {str(e)}"})


# ============================================================================
# Main Entry Point
# ============================================================================

def main():
    """Run the MCP server."""
    print(f"Starting ELF MCP Server for client: {CLIENT_ID}", file=sys.stderr)
    print(f"Database: {get_client_elf_db()}", file=sys.stderr)
    mcp.run()


if __name__ == "__main__":
    main()
