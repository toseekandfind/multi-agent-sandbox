"""
ELF - Emergent Learning Framework for persistent memory and pattern tracking.

This module is adapted from https://github.com/Spacehunterz/Emergent-Learning-Framework_ELF
and provides cross-session learning, heuristics, and pheromone trails for AI agents.

Key components:
- query/: Core query and memory system
  - query.py: Main query interface
  - core.py: Core memory operations
  - dashboard.py: Web dashboard
  - models.py: Data models
  - lifecycle_manager.py: Session lifecycle

- conductor/: Workflow orchestration
  - conductor.py: Main conductor
  - executor.py: Node execution
  - schema.sql: Database schema

- watcher/: Background monitoring
- agents/: Swarm agent definitions
- skills/: Skill definitions
"""

# Lazy import to avoid heavy startup
def get_memory():
    """Get the ELF memory interface."""
    from .query.core import get_memory_interface
    return get_memory_interface()

def get_conductor():
    """Get the ELF conductor for workflow orchestration."""
    from .conductor.conductor import Conductor
    return Conductor()
