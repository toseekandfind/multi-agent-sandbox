"""
ELF Memory Interface - Simplified sync wrapper for worker integration.

Provides:
- get_context(): Retrieve heuristics and golden rules before job execution
- record_outcome(): Record job outcomes to build learning over time
- get_golden_rules(): Get high-confidence rules (is_golden=True)
"""

import sqlite3
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any


class ELFMemory:
    """
    Simplified synchronous interface to ELF memory.

    Usage:
        memory = ELFMemory()

        # Before job execution
        context = memory.get_context(
            project_path="/workspace/my-project",
            domain="authentication"
        )

        # After job execution
        memory.record_outcome(
            project_path="/workspace/my-project",
            job_type="agent_farm",
            outcome="success",
            learnings=["Token refresh needs error handling"],
            files_touched=["auth.py", "tokens.py"]
        )
    """

    DEFAULT_DB_PATH = Path.home() / ".claude" / "emergent-learning" / "memory" / "index.db"

    def __init__(self, db_path: Optional[str] = None):
        """
        Initialize ELF memory interface.

        Args:
            db_path: Path to SQLite database. Defaults to ~/.claude/emergent-learning/memory/index.db
        """
        if db_path:
            self.db_path = Path(db_path)
        else:
            self.db_path = self.DEFAULT_DB_PATH

        # Ensure directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Initialize database if needed
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        """Get a database connection."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """Initialize database tables if they don't exist."""
        with self._get_conn() as conn:
            # Create heuristics table (simplified schema matching ELF)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS heuristics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    domain TEXT NOT NULL,
                    rule TEXT NOT NULL,
                    explanation TEXT,
                    source_type TEXT,
                    source_id INTEGER,
                    confidence REAL DEFAULT 0.5,
                    times_validated INTEGER DEFAULT 0,
                    times_violated INTEGER DEFAULT 0,
                    is_golden INTEGER DEFAULT 0,
                    project_path TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Create learnings table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS learnings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    type TEXT NOT NULL,
                    filepath TEXT,
                    title TEXT NOT NULL,
                    summary TEXT,
                    tags TEXT,
                    domain TEXT,
                    severity INTEGER DEFAULT 3,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Create trails table (pheromone tracking)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trails (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    location TEXT NOT NULL,
                    location_type TEXT DEFAULT 'file',
                    scent TEXT NOT NULL,
                    strength REAL DEFAULT 1.0,
                    agent_id TEXT,
                    message TEXT,
                    tags TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    expires_at TEXT
                )
            """)

            # Create job_outcomes table (new - for tracking job results)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS job_outcomes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    job_type TEXT NOT NULL,
                    project_path TEXT,
                    outcome TEXT NOT NULL,
                    duration_seconds REAL,
                    agent_count INTEGER,
                    files_touched TEXT,
                    learnings_json TEXT,
                    error_message TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Create indexes
            conn.execute("CREATE INDEX IF NOT EXISTS idx_heuristics_domain ON heuristics(domain)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_heuristics_project ON heuristics(project_path)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_heuristics_golden ON heuristics(is_golden)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_learnings_domain ON learnings(domain)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_trails_location ON trails(location)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_job_outcomes_project ON job_outcomes(project_path)")

            conn.commit()

    def get_golden_rules(self, project_path: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get golden rules (high-confidence heuristics).

        Args:
            project_path: Filter by project path (also includes global rules)

        Returns:
            List of golden rule dictionaries
        """
        with self._get_conn() as conn:
            if project_path:
                # Get project-specific and global golden rules
                cursor = conn.execute("""
                    SELECT * FROM heuristics
                    WHERE is_golden = 1
                    AND (project_path IS NULL OR project_path = ?)
                    ORDER BY confidence DESC, times_validated DESC
                """, (project_path,))
            else:
                cursor = conn.execute("""
                    SELECT * FROM heuristics
                    WHERE is_golden = 1
                    ORDER BY confidence DESC, times_validated DESC
                """)

            return [dict(row) for row in cursor.fetchall()]

    def get_heuristics(
        self,
        domain: Optional[str] = None,
        project_path: Optional[str] = None,
        min_confidence: float = 0.3,
        limit: int = 20
    ) -> List[Dict[str, Any]]:
        """
        Get relevant heuristics for a domain/project.

        Args:
            domain: Filter by domain (e.g., "authentication", "database")
            project_path: Filter by project path
            min_confidence: Minimum confidence threshold
            limit: Maximum number of heuristics to return

        Returns:
            List of heuristic dictionaries
        """
        with self._get_conn() as conn:
            conditions = ["confidence >= ?"]
            params: List[Any] = [min_confidence]

            if domain:
                conditions.append("domain = ?")
                params.append(domain)

            if project_path:
                conditions.append("(project_path IS NULL OR project_path = ?)")
                params.append(project_path)

            params.append(limit)

            query = f"""
                SELECT * FROM heuristics
                WHERE {' AND '.join(conditions)}
                ORDER BY confidence DESC, times_validated DESC
                LIMIT ?
            """

            cursor = conn.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def get_context(
        self,
        project_path: Optional[str] = None,
        domain: Optional[str] = None,
        files: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Get full context for a job (golden rules + heuristics + trails).

        Args:
            project_path: Project being worked on
            domain: Specific domain focus
            files: List of files that will be worked on

        Returns:
            Dictionary with golden_rules, heuristics, trails, and formatted prompt context
        """
        golden_rules = self.get_golden_rules(project_path)
        heuristics = self.get_heuristics(domain=domain, project_path=project_path)

        # Get recent trails for the files
        trails = []
        if files:
            with self._get_conn() as conn:
                placeholders = ",".join("?" * len(files))
                cursor = conn.execute(f"""
                    SELECT * FROM trails
                    WHERE location IN ({placeholders})
                    AND (expires_at IS NULL OR expires_at > datetime('now'))
                    ORDER BY created_at DESC
                    LIMIT 50
                """, files)
                trails = [dict(row) for row in cursor.fetchall()]

        # Get recent job outcomes for this project
        recent_outcomes = []
        if project_path:
            with self._get_conn() as conn:
                cursor = conn.execute("""
                    SELECT * FROM job_outcomes
                    WHERE project_path = ?
                    ORDER BY created_at DESC
                    LIMIT 10
                """, (project_path,))
                recent_outcomes = [dict(row) for row in cursor.fetchall()]

        # Format as prompt context
        context_parts = []

        if golden_rules:
            context_parts.append("## Golden Rules (Always Follow)")
            for rule in golden_rules:
                context_parts.append(f"- {rule['rule']}")
            context_parts.append("")

        if heuristics:
            context_parts.append("## Learned Patterns")
            for h in heuristics[:10]:  # Limit to top 10
                conf = h.get('confidence', 0.5)
                validated = h.get('times_validated', 0)
                context_parts.append(f"- [{conf:.0%}] {h['rule']} (validated {validated}x)")
            context_parts.append("")

        if trails:
            context_parts.append("## Recent Activity on These Files")
            for t in trails[:5]:
                context_parts.append(f"- {t['location']}: {t['scent']} - {t.get('message', '')}")
            context_parts.append("")

        if recent_outcomes:
            failures = [o for o in recent_outcomes if o['outcome'] == 'failure']
            if failures:
                context_parts.append("## Recent Issues")
                for f in failures[:3]:
                    context_parts.append(f"- {f['job_type']}: {f.get('error_message', 'Unknown error')}")
                context_parts.append("")

        return {
            "golden_rules": golden_rules,
            "heuristics": heuristics,
            "trails": trails,
            "recent_outcomes": recent_outcomes,
            "prompt_context": "\n".join(context_parts) if context_parts else ""
        }

    def record_outcome(
        self,
        job_id: str,
        job_type: str,
        outcome: str,  # "success" | "failure" | "partial"
        project_path: Optional[str] = None,
        duration_seconds: Optional[float] = None,
        agent_count: Optional[int] = None,
        files_touched: Optional[List[str]] = None,
        learnings: Optional[List[str]] = None,
        error_message: Optional[str] = None
    ) -> int:
        """
        Record a job outcome for future learning.

        Args:
            job_id: Unique job identifier
            job_type: Type of job (e.g., "agent_farm", "claude_chat")
            outcome: Result - "success", "failure", or "partial"
            project_path: Project that was worked on
            duration_seconds: How long the job took
            agent_count: Number of agents used
            files_touched: List of files modified
            learnings: List of lessons learned (strings)
            error_message: Error message if failed

        Returns:
            ID of the created outcome record
        """
        with self._get_conn() as conn:
            cursor = conn.execute("""
                INSERT INTO job_outcomes
                (job_id, job_type, project_path, outcome, duration_seconds,
                 agent_count, files_touched, learnings_json, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                job_id,
                job_type,
                project_path,
                outcome,
                duration_seconds,
                agent_count,
                json.dumps(files_touched) if files_touched else None,
                json.dumps(learnings) if learnings else None,
                error_message
            ))
            conn.commit()

            outcome_id = cursor.lastrowid

            # Update trails for files touched
            if files_touched and outcome_id:
                scent = "success" if outcome == "success" else "warning" if outcome == "partial" else "failure"
                for file_path in files_touched:
                    self.add_trail(
                        location=file_path,
                        scent=scent,
                        message=f"Job {job_id[:8]}: {outcome}"
                    )

            return outcome_id

    def add_trail(
        self,
        location: str,
        scent: str,
        message: Optional[str] = None,
        agent_id: Optional[str] = None,
        strength: float = 1.0,
        expires_hours: Optional[int] = 24
    ):
        """
        Add a pheromone trail (breadcrumb) for a location.

        Args:
            location: File path or identifier
            scent: Type of trail - "success", "warning", "failure", "hot", "cold"
            message: Description of what happened
            agent_id: ID of agent that left the trail
            strength: Trail strength (0.0-1.0)
            expires_hours: Hours until trail expires (None = never)
        """
        expires_at = None
        if expires_hours:
            from datetime import timedelta
            expires_at = (datetime.utcnow() + timedelta(hours=expires_hours)).isoformat()

        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO trails (location, scent, message, agent_id, strength, expires_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (location, scent, message, agent_id, strength, expires_at))
            conn.commit()

    def add_heuristic(
        self,
        domain: str,
        rule: str,
        explanation: Optional[str] = None,
        project_path: Optional[str] = None,
        confidence: float = 0.5,
        source_type: Optional[str] = None,
        source_id: Optional[int] = None
    ) -> int:
        """
        Add a new heuristic (learned pattern).

        Args:
            domain: Domain the heuristic applies to
            rule: The rule/pattern itself
            explanation: Why this rule matters
            project_path: Project-specific (None = global)
            confidence: Initial confidence (0.0-1.0)
            source_type: Where this came from (e.g., "job_outcome")
            source_id: ID of the source record

        Returns:
            ID of the created heuristic
        """
        with self._get_conn() as conn:
            cursor = conn.execute("""
                INSERT INTO heuristics
                (domain, rule, explanation, project_path, confidence, source_type, source_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (domain, rule, explanation, project_path, confidence, source_type, source_id))
            conn.commit()
            return cursor.lastrowid

    def validate_heuristic(self, heuristic_id: int):
        """Mark a heuristic as validated (increases confidence)."""
        with self._get_conn() as conn:
            conn.execute("""
                UPDATE heuristics
                SET times_validated = times_validated + 1,
                    confidence = MIN(1.0, confidence + 0.05),
                    is_golden = CASE WHEN confidence >= 0.9 THEN 1 ELSE is_golden END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (heuristic_id,))
            conn.commit()

    def violate_heuristic(self, heuristic_id: int):
        """Mark a heuristic as violated (decreases confidence)."""
        with self._get_conn() as conn:
            conn.execute("""
                UPDATE heuristics
                SET times_violated = times_violated + 1,
                    confidence = MAX(0.0, confidence - 0.1),
                    is_golden = CASE WHEN confidence < 0.9 THEN 0 ELSE is_golden END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (heuristic_id,))
            conn.commit()

    def get_stats(self) -> Dict[str, Any]:
        """Get memory statistics."""
        with self._get_conn() as conn:
            stats = {}

            cursor = conn.execute("SELECT COUNT(*) FROM heuristics")
            stats["total_heuristics"] = cursor.fetchone()[0]

            cursor = conn.execute("SELECT COUNT(*) FROM heuristics WHERE is_golden = 1")
            stats["golden_rules"] = cursor.fetchone()[0]

            cursor = conn.execute("SELECT COUNT(*) FROM learnings")
            stats["total_learnings"] = cursor.fetchone()[0]

            cursor = conn.execute("SELECT COUNT(*) FROM trails WHERE expires_at IS NULL OR expires_at > datetime('now')")
            stats["active_trails"] = cursor.fetchone()[0]

            cursor = conn.execute("SELECT COUNT(*) FROM job_outcomes")
            stats["total_outcomes"] = cursor.fetchone()[0]

            cursor = conn.execute("SELECT COUNT(*) FROM job_outcomes WHERE outcome = 'success'")
            stats["successful_jobs"] = cursor.fetchone()[0]

            cursor = conn.execute("SELECT COUNT(*) FROM job_outcomes WHERE outcome = 'failure'")
            stats["failed_jobs"] = cursor.fetchone()[0]

            return stats


# Convenience function for getting memory interface
def get_memory(db_path: Optional[str] = None) -> ELFMemory:
    """Get an ELF memory interface instance."""
    return ELFMemory(db_path=db_path)
