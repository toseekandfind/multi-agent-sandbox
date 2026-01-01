#!/usr/bin/env python3
"""
dbt Changed Selector

Computes a dbt selector string based on git diff between current branch and main.
Outputs a selector like: +model_a+ +model_b+ +model_c+

Usage:
    python scripts/dbt_changed_selector.py

    # Or with custom base branch
    python scripts/dbt_changed_selector.py --base origin/develop

    # Output only (for CI pipelines)
    SELECTOR=$(python scripts/dbt_changed_selector.py)
    dbt build --select "$SELECTOR"
"""

import argparse
import subprocess
import sys
import re
from pathlib import Path
from typing import List, Set


def run_git_diff(base_branch: str = "origin/main") -> List[str]:
    """Get list of changed files compared to base branch."""
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", f"{base_branch}...HEAD"],
            capture_output=True,
            text=True,
            check=True
        )
        return [f.strip() for f in result.stdout.strip().split("\n") if f.strip()]
    except subprocess.CalledProcessError as e:
        print(f"Error running git diff: {e.stderr}", file=sys.stderr)
        # Fallback: try comparing to local main
        try:
            result = subprocess.run(
                ["git", "diff", "--name-only", "main...HEAD"],
                capture_output=True,
                text=True,
                check=True
            )
            return [f.strip() for f in result.stdout.strip().split("\n") if f.strip()]
        except subprocess.CalledProcessError:
            return []


def extract_model_name(filepath: str) -> str:
    """Extract model name from a SQL file path."""
    path = Path(filepath)
    return path.stem  # filename without extension


def extract_directory(filepath: str) -> str:
    """Extract the directory path for a file."""
    path = Path(filepath)
    return str(path.parent)


def parse_changed_files(changed_files: List[str]) -> dict:
    """
    Parse changed files into categories.

    Returns:
        dict with keys: models, yml_dirs, macros, seeds, snapshots
    """
    result = {
        "models": set(),      # Model names to select
        "yml_dirs": set(),    # Directories with changed yml files
        "macros": set(),      # Changed macro files (triggers smoke test)
        "seeds": set(),       # Seed names to select
        "snapshots": set(),   # Snapshot names to select
    }

    for filepath in changed_files:
        # Model SQL files
        if filepath.startswith("models/") and filepath.endswith(".sql"):
            model_name = extract_model_name(filepath)
            result["models"].add(model_name)

        # Model YAML files (schema.yml, etc.)
        elif filepath.startswith("models/") and (filepath.endswith(".yml") or filepath.endswith(".yaml")):
            # For yml changes, we select the whole directory
            directory = extract_directory(filepath)
            result["yml_dirs"].add(directory)

        # Macro files
        elif filepath.startswith("macros/") and filepath.endswith(".sql"):
            result["macros"].add(filepath)

        # Seed files
        elif filepath.startswith("seeds/") and filepath.endswith(".csv"):
            seed_name = extract_model_name(filepath)
            result["seeds"].add(seed_name)

        # Seed YAML files
        elif filepath.startswith("seeds/") and (filepath.endswith(".yml") or filepath.endswith(".yaml")):
            directory = extract_directory(filepath)
            result["yml_dirs"].add(directory)

        # Snapshot files
        elif filepath.startswith("snapshots/") and filepath.endswith(".sql"):
            snapshot_name = extract_model_name(filepath)
            result["snapshots"].add(snapshot_name)

    return result


def build_selector(parsed: dict) -> str:
    """
    Build a dbt selector string from parsed changes.

    Selection rules:
    - Models: +model_name+ (include upstream and downstream)
    - YML dirs: +path:models/dir+ (include all in directory + deps)
    - Macros: +tag:ci_smoke+ (smoke test when macros change)
    - Seeds: +seed_name+ (include downstream)
    - Snapshots: +snapshot_name+ (include downstream)
    """
    selectors = []

    # Add model selectors
    for model in sorted(parsed["models"]):
        selectors.append(f"+{model}+")

    # Add directory selectors for yml changes
    for directory in sorted(parsed["yml_dirs"]):
        selectors.append(f"+path:{directory}+")

    # Add seed selectors
    for seed in sorted(parsed["seeds"]):
        selectors.append(f"+{seed}+")

    # Add snapshot selectors
    for snapshot in sorted(parsed["snapshots"]):
        selectors.append(f"+{snapshot}+")

    # If macros changed, add smoke test selector
    if parsed["macros"]:
        # If we have other selectors, combine them with smoke test
        if selectors:
            selectors.append("+tag:ci_smoke+")
        else:
            # Only macros changed, just run smoke tests
            selectors.append("+tag:ci_smoke+")

    return " ".join(selectors)


def get_smoke_selector() -> str:
    """Return the fallback smoke test selector."""
    return "+tag:ci_smoke+"


def main():
    parser = argparse.ArgumentParser(
        description="Generate dbt selector based on git diff"
    )
    parser.add_argument(
        "--base",
        default="origin/main",
        help="Base branch to compare against (default: origin/main)"
    )
    parser.add_argument(
        "--smoke-fallback",
        action="store_true",
        help="If no changes detected, output smoke test selector instead of empty"
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print detailed info to stderr"
    )

    args = parser.parse_args()

    # Get changed files
    changed_files = run_git_diff(args.base)

    if args.verbose:
        print(f"Changed files ({len(changed_files)}):", file=sys.stderr)
        for f in changed_files:
            print(f"  {f}", file=sys.stderr)

    # Parse changes
    parsed = parse_changed_files(changed_files)

    if args.verbose:
        print(f"\nParsed changes:", file=sys.stderr)
        print(f"  Models: {parsed['models']}", file=sys.stderr)
        print(f"  YML dirs: {parsed['yml_dirs']}", file=sys.stderr)
        print(f"  Macros: {parsed['macros']}", file=sys.stderr)
        print(f"  Seeds: {parsed['seeds']}", file=sys.stderr)
        print(f"  Snapshots: {parsed['snapshots']}", file=sys.stderr)

    # Build selector
    selector = build_selector(parsed)

    # Handle empty selector
    if not selector:
        if args.smoke_fallback:
            selector = get_smoke_selector()
            if args.verbose:
                print(f"\nNo dbt changes detected, using smoke fallback", file=sys.stderr)
        else:
            if args.verbose:
                print(f"\nNo dbt changes detected", file=sys.stderr)

    if args.verbose:
        print(f"\nSelector: {selector}", file=sys.stderr)

    # Output selector (this is what CI will capture)
    print(selector)

    return 0


if __name__ == "__main__":
    sys.exit(main())
