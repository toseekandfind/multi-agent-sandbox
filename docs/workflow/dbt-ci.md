# dbt CI Workflow Documentation

This document describes the CI workflow for dbt projects in this repository.

## Overview

Our CI pipeline runs **scoped dbt builds** - we only build and test models that changed plus their upstream and downstream dependencies. This approach:

- Avoids expensive full-project builds
- Provides fast feedback on PRs
- Catches breaking changes in dependencies
- Uses isolated CI schemas that are cleaned up after each run

## Required CI Checks

The following checks are **required** and block merging to main:

| Check | Description |
|-------|-------------|
| `ci/dbt-compile` | Validates that all models compile without errors |
| `ci/dbt-build-scoped` | Builds changed models + dependencies |
| `ci/dbt-test-scoped` | Runs tests on changed models + dependencies |

### What We Don't Check

- Source freshness (handled separately in production)
- SQL linting (sqlfluff not required)
- Docs generation (done in production only)
- Full project build (too slow, not scoped)

## Scoped Selection Strategy

We do **not** rely on `state:modified+` because CI may not have access to dbt state artifacts from previous runs.

Instead, we compute changed models from git diff and run dbt with explicit selectors.

### Selection Rule

For each changed model, we run:
```bash
dbt build --select +<model>+
```

The `+` prefix includes upstream dependencies, and the `+` suffix includes downstream dependencies.

For multiple changed models:
```bash
dbt build --select +model_a+ +model_b+ +model_c+
```

### How Changed Models Are Computed

The `scripts/dbt_changed_selector.py` script:

1. Runs `git diff --name-only origin/main...HEAD`
2. Filters for dbt-relevant files
3. Generates appropriate dbt selectors

#### File Type Mapping

| File Pattern | Selector Generated |
|--------------|-------------------|
| `models/**/*.sql` | `+<model_name>+` |
| `models/**/*.yml` | `+path:models/<dir>+` (directory-level) |
| `macros/**/*.sql` | `+tag:ci_smoke+` (smoke test fallback) |
| `seeds/**/*.csv` | `+<seed_name>+` |
| `snapshots/**/*.sql` | `+<snapshot_name>+` |

#### Macro Change Handling

When macros change, determining impact is complex. We use the `ci_smoke` tag convention:

1. Tag key models in your project with `+ci_smoke`
2. When macros change, CI runs: `dbt build --select +tag:ci_smoke+`
3. This provides bounded coverage without full-project builds

To tag models, add to your model config:
```yaml
# models/core/schema.yml
models:
  - name: dim_customers
    config:
      tags: ['ci_smoke']
  - name: fct_orders
    config:
      tags: ['ci_smoke']
```

Or in the model SQL:
```sql
{{ config(tags=['ci_smoke']) }}
```

#### Empty Selector Handling

If no dbt-relevant files changed:
- CI runs a minimal smoke test: `dbt build --select tag:ci_smoke`
- If no `ci_smoke` tags exist, CI passes with a warning

### Seeds and Snapshots

**Seeds:**
```bash
# First seed the changed files
dbt seed --select <seed_names>
# Then build dependents
dbt build --select +<seed_name>+
```

**Snapshots:**
- Do NOT run `--full-refresh` by default in CI
- Run snapshot + dependent tests
- Full refresh is a manual production operation

## CI Schema Isolation

Each PR runs in an isolated schema/database to avoid conflicts with dev or production.

### Schema Naming Pattern

```
ci_<pr_number>_<short_sha>
```

Examples:
- `ci_123_abc1234` (Snowflake schema)
- `ci_123_abc1234` (BigQuery dataset)
- `ci_123_abc1234` (Databricks schema)

### Cleanup

CI schemas are cleaned up:
- As a final CI step (runs even if earlier steps fail)
- Via scheduled cleanup job for orphaned schemas

## Warehouse CI Targets

### profiles.yml Configuration

Add a `ci` target to your profiles.yml:

```yaml
# profiles.yml
my_dbt_project:
  target: dev
  outputs:
    dev:
      type: snowflake
      account: "{{ env_var('SNOWFLAKE_ACCOUNT') }}"
      user: "{{ env_var('SNOWFLAKE_USER') }}"
      password: "{{ env_var('SNOWFLAKE_PASSWORD') }}"
      role: "{{ env_var('SNOWFLAKE_ROLE') }}"
      warehouse: "{{ env_var('SNOWFLAKE_WAREHOUSE') }}"
      database: analytics_dev
      schema: "{{ env_var('DBT_SCHEMA', 'dbt_' ~ env_var('USER', 'dev')) }}"
      threads: 4

    ci:
      type: snowflake
      account: "{{ env_var('SNOWFLAKE_ACCOUNT') }}"
      user: "{{ env_var('SNOWFLAKE_USER') }}"
      password: "{{ env_var('SNOWFLAKE_PASSWORD') }}"
      role: "{{ env_var('SNOWFLAKE_ROLE') }}"
      warehouse: "{{ env_var('SNOWFLAKE_WAREHOUSE') }}"
      database: analytics_ci
      schema: "{{ env_var('DBT_CI_SCHEMA') }}"  # Set dynamically in CI
      threads: 4
```

### Per-Warehouse Examples

#### Snowflake
```yaml
ci:
  type: snowflake
  database: analytics_ci
  schema: "{{ env_var('DBT_CI_SCHEMA') }}"
```

#### BigQuery
```yaml
ci:
  type: bigquery
  project: my-project-ci
  dataset: "{{ env_var('DBT_CI_SCHEMA') }}"
```

#### Databricks
```yaml
ci:
  type: databricks
  catalog: ci_catalog
  schema: "{{ env_var('DBT_CI_SCHEMA') }}"
```

## Required Environment Variables

These must be set in your CI secrets store:

| Variable | Description |
|----------|-------------|
| `SNOWFLAKE_ACCOUNT` | Snowflake account identifier |
| `SNOWFLAKE_USER` | CI service account username |
| `SNOWFLAKE_PASSWORD` | CI service account password |
| `SNOWFLAKE_ROLE` | Role with CI database access |
| `SNOWFLAKE_WAREHOUSE` | Warehouse for CI runs |
| `DBT_CI_SCHEMA` | Dynamically set per PR |

For BigQuery:
| Variable | Description |
|----------|-------------|
| `GOOGLE_APPLICATION_CREDENTIALS` | Path to service account JSON |
| `DBT_CI_SCHEMA` | Dynamically set per PR |

For Databricks:
| Variable | Description |
|----------|-------------|
| `DATABRICKS_HOST` | Workspace URL |
| `DATABRICKS_TOKEN` | Service principal token |
| `DBT_CI_SCHEMA` | Dynamically set per PR |

## Local Development

### Running CI Checks Locally

Use the verify script to run the same checks CI runs:

```bash
# Run full verification (compile + scoped build)
./scripts/verify

# Run with explicit target
DBT_TARGET=ci ./scripts/verify
```

### Testing Selector Generation

```bash
# See what selector would be generated
python scripts/dbt_changed_selector.py

# Example output: +dim_customers+ +fct_orders+
```

## Troubleshooting

### "No models selected"

If CI reports no models selected:
1. Check if your changes are in dbt-relevant paths
2. Verify `ci_smoke` tags exist if only macros changed
3. Check git diff output: `git diff --name-only origin/main...HEAD`

### CI Schema Not Cleaned Up

If CI schemas accumulate:
1. Check if cleanup step failed
2. Run manual cleanup: `dbt run-operation drop_ci_schema --args '{schema: ci_xxx_yyy}'`
3. Review scheduled cleanup job logs

### Compile Succeeds But Build Fails

1. Check if upstream models exist in CI schema
2. Verify selector includes all dependencies (`+model+` not just `model`)
3. Check for missing seeds that need to be run first

## Agent-Assisted Workflow

This repo supports multi-agent orchestration for dbt workflows using three specialized agent roles.

### Agent Roles

| Role | Prompt File | Responsibilities |
|------|-------------|------------------|
| **Lead** | `agent_farm/prompts/dbt_agent_lead.txt` | Owns guardrails, coordinates worker & reviewer |
| **Worker** | `agent_farm/prompts/dbt_worker_agent.txt` | Implements changes on feature branch |
| **Reviewer** | `agent_farm/prompts/dbt_review_agent.txt` | Reviews PRs, checks for risky actions |

### Workflow Sequence

```
┌─────────────────────────────────────────────────────────────┐
│                      LEAD AGENT                              │
│  - Receives task                                            │
│  - Creates task breakdown                                   │
│  - Enforces guardrails                                      │
└─────────────────┬───────────────────────┬───────────────────┘
                  │                       │
                  ▼                       ▼
┌─────────────────────────┐   ┌─────────────────────────┐
│     WORKER AGENT        │   │     REVIEWER AGENT      │
│  - Creates feature      │   │  - Reviews PR diff      │
│    branch               │──▶│  - Checks guardrails    │
│  - Implements changes   │   │  - Approves or requests │
│  - Runs ./scripts/verify│   │    changes              │
│  - Commits and pushes   │   │                         │
└─────────────────────────┘   └─────────────────────────┘
                  │                       │
                  └───────────┬───────────┘
                              ▼
                  ┌─────────────────────────┐
                  │      LEAD AGENT         │
                  │  - Merges if approved   │
                  │  - Reports completion   │
                  └─────────────────────────┘
```

### Using Agents via VPS

Submit an agent workflow job to the VPS:

```bash
# Single agent (e.g., just worker)
curl -X POST http://151.243.109.200:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "job_type": "agent_farm",
    "payload": {
      "repo": "/path/to/dbt/project",
      "prompt_file": "prompts/dbt_worker_agent.txt",
      "config_file": "configs/dbt_analytics_config.json",
      "task": "Add a new dim_products model with tests"
    }
  }'
```

### Using Agents Locally

Run the agent farm directly:

```bash
cd agent_farm

# Run with dbt config
python claude_code_agent_farm.py \
  --config configs/dbt_analytics_config.json \
  --prompt prompts/dbt_worker_agent.txt \
  --repo /path/to/dbt/project \
  --agents 1
```

### Agent Guardrails

All agents enforce these rules (configured in `dbt_analytics_config.json`):

| Guardrail | Description |
|-----------|-------------|
| `no_direct_push_to_main` | All changes via feature branch + PR |
| `no_force_push` | Never rewrite shared history |
| `require_ticket_reference` | Commits must reference tickets |
| `require_ci_pass` | CI must pass before merge |
| `require_reviewer_approval` | Reviewer agent must approve |
| `no_full_project_build` | Always use scoped selectors |
| `no_snapshot_full_refresh` | No --full-refresh without approval |
| `no_hardcoded_credentials` | All secrets via env_var() |

### Example: Full Workflow

1. **Lead receives task**: "Add customer lifetime value to dim_customers"

2. **Lead delegates to Worker**:
   ```
   WORKER TASK:
   - Branch: feature/AE-123-add-customer-ltv
   - Objective: Add ltv_amount and ltv_segment columns
   - Files: models/marts/dim_customers.sql, models/marts/schema.yml
   - Run: ./scripts/verify before commit
   ```

3. **Worker implements**:
   ```bash
   git checkout -b feature/AE-123-add-customer-ltv
   # ... makes changes ...
   ./scripts/verify  # passes
   git commit -m "[AE-123] Add customer LTV columns"
   git push -u origin feature/AE-123-add-customer-ltv
   ```

4. **Lead triggers Reviewer**:
   ```
   REVIEW REQUEST:
   - PR: feature/AE-123-add-customer-ltv
   - Changes: 2 files, adds 2 columns + tests
   ```

5. **Reviewer checks and approves**:
   ```
   REVIEW APPROVED:
   - CI passed: YES
   - Guardrails verified: YES
   - Ready to merge: YES
   ```

6. **Lead merges**:
   ```bash
   git checkout main
   git merge --squash feature/AE-123-add-customer-ltv
   git push origin main
   ```
