## Ticket

<!-- Link to the ticket (Jira, Linear, GitHub Issue, etc.) -->
[TICKET-XXX](https://link-to-ticket)

## What Changed

<!-- List the models, tests, macros, seeds, or snapshots that were modified -->

### Models
- [ ] `models/...`

### Tests
- [ ] `tests/...`

### Macros
- [ ] `macros/...`

### Seeds
- [ ] `seeds/...`

### Other
- [ ] Configuration changes
- [ ] Documentation updates

## Data Impact

<!-- Describe how this change affects data -->

### Schema Changes
<!-- New columns, removed columns, type changes -->
- None / Describe changes

### Grain Changes
<!-- Does this change the grain of any model? -->
- None / Describe changes

### Expected Row Deltas
<!-- Will row counts change significantly? -->
- None / Describe expected changes

## Backfill Required?

- [ ] **No** - Changes apply going forward only
- [ ] **Yes** - Historical data needs to be reprocessed

<!-- If yes, describe the backfill process -->
**Backfill Process:**
```bash
# Commands to run for backfill
```

## Rollback Plan

<!-- How to revert this change if something goes wrong -->

```bash
# Example rollback commands
git revert <commit-sha>
dbt build --select +<affected_models>+
```

## Evidence

<!-- Proof that changes work correctly -->

### CI Status
<!-- CI will automatically update this section -->
- [ ] `ci/dbt-compile` - Pending
- [ ] `ci/dbt-build-scoped` - Pending
- [ ] `ci/dbt-test-scoped` - Pending

### Local Testing
<!-- Paste relevant output from local runs -->
```
# ./scripts/verify output or dbt command output
```

### Data Validation
<!-- Screenshots, query results, or other evidence that data is correct -->


## Checklist

- [ ] I have tested these changes locally
- [ ] CI checks are passing
- [ ] I have updated documentation if needed
- [ ] I have added/updated tests for new models
- [ ] I have verified data impact is as expected
- [ ] Rollback plan is documented
