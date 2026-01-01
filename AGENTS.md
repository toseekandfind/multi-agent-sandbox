# Instructions for AI Agents

This file provides instructions for AI assistants (Claude, GPT, etc.) helping users with this repository.

## First-Time Setup

When a user asks to set up this project, follow these steps:

### 1. Check Prerequisites

Verify these tools are installed:
```bash
docker --version
aws --version
terraform --version
```

If any are missing, help the user install them first.

### 2. Gather Required Information

**Ask the user these questions:**

1. **AWS Profile**: "What AWS CLI profile should I use? Run `aws configure list-profiles` to see available profiles."

2. **AWS Region**: "What AWS region should I deploy to? (default: us-east-1)"

3. **Anthropic API Key** (optional): "Do you have an Anthropic API key for Claude integration? If not, you can get one at console.anthropic.com/settings/keys, or skip this for echo-only testing."

### 3. Verify AWS Access

```bash
aws sts get-caller-identity --profile <PROFILE>
```

If this fails, help configure credentials with `aws configure --profile <PROFILE>`.

### 4. Create Terraform Variables

Create `infra/terraform/terraform.tfvars`:
```hcl
aws_profile = "<USER_PROVIDED_PROFILE>"
aws_region  = "<USER_PROVIDED_REGION>"
```

### 5. Deploy Infrastructure

```bash
cd infra/terraform
terraform init
terraform plan
# Ask user to review plan before applying
terraform apply
```

### 6. Store API Key (if provided)

```bash
aws secretsmanager create-secret \
  --name agent-runner/anthropic-api-key \
  --secret-string "<USER_PROVIDED_API_KEY>" \
  --profile <PROFILE> --region <REGION>
```

### 7. Build and Push Images

Get account ID first:
```bash
ACCOUNT_ID=$(aws sts get-caller-identity --profile <PROFILE> --query Account --output text)
```

Then build and push:
```bash
# Login to ECR
aws ecr get-login-password --region <REGION> --profile <PROFILE> | \
  docker login --username AWS --password-stdin $ACCOUNT_ID.dkr.ecr.<REGION>.amazonaws.com

# Build and push both images
docker build --platform linux/amd64 -t $ACCOUNT_ID.dkr.ecr.<REGION>.amazonaws.com/agent-runner-control-api:latest control_api/
docker push $ACCOUNT_ID.dkr.ecr.<REGION>.amazonaws.com/agent-runner-control-api:latest

docker build --platform linux/amd64 -t $ACCOUNT_ID.dkr.ecr.<REGION>.amazonaws.com/agent-runner-worker:latest worker/
docker push $ACCOUNT_ID.dkr.ecr.<REGION>.amazonaws.com/agent-runner-worker:latest
```

### 8. Deploy and Verify

```bash
aws ecs update-service --cluster agent-runner --service orchestrator \
  --force-new-deployment --profile <PROFILE> --region <REGION>

# Wait and check status
sleep 60
aws ecs describe-services --cluster agent-runner --services orchestrator \
  --profile <PROFILE> --region <REGION> \
  --query 'services[0].{status:status,running:runningCount}'
```

---

## Branching and PR Workflow

This project follows a **main-only workflow** with feature branch PRs.

### Branch Naming Convention

All feature branches must follow this pattern:
```
feature/<ticket-id>-<short-slug>
```

Examples:
- `feature/AE-123-add-customer-dim`
- `feature/JIRA-456-fix-revenue-calc`
- `feature/GH-789-add-ci-smoke-tests`

### PR Flow to Main

1. **Create feature branch** from `main`:
   ```bash
   git checkout main
   git pull origin main
   git checkout -b feature/<ticket>-<slug>
   ```

2. **Make changes** and commit with clear messages

3. **Push and open PR** to `main`:
   ```bash
   git push -u origin feature/<ticket>-<slug>
   ```

4. **Address review feedback** via commits on the same branch (do not open new PRs)

5. **Squash merge** once approved

### Commit and Merge Rules

- **No direct pushes to main** - main is protected
- **All changes via PR** - even small fixes
- **All feedback addressed via commits on same branch** - no new PRs for review fixes
- **Prefer squash merge** - one commit per feature/fix

### Squash Commit Message Format

When squash merging, the commit message must include:

```
[TICKET-123] Short summary of change

Data Impact: <describe schema changes, grain changes, expected row deltas>
Rollback: <steps to revert if needed>
```

Example:
```
[AE-456] Add customer lifetime value to dim_customers

Data Impact: Adds 2 new columns (ltv_amount, ltv_segment). No grain change.
             Existing rows will have NULL for historical periods.
Rollback: Revert commit and run dbt build --select dim_customers
```

### Agent Roles for dbt Projects

| Role | Responsibilities |
|------|-----------------|
| **Agent Lead** | Owns workflow docs + guardrails, coordinates worker + reviewer agents |
| **Worker Agent** | Implements repo changes on feature branch, adds scripts and CI updates |
| **Review Agent** | Reviews PR diffs, checks for risky git actions and missing guardrails |

---

## Common Tasks

### Local Development Only

If user just wants to test locally (no AWS):
```bash
docker-compose -f compose/docker-compose.yml up --build
curl http://localhost:8000/health
```

### Stop AWS Spending

```bash
aws ecs update-service --cluster agent-runner --service orchestrator \
  --desired-count 0 --profile <PROFILE> --region <REGION>
```

### Full Cleanup

```bash
cd infra/terraform
terraform destroy
```

### Submit Test Job

See SETUP.md for the full command sequence to submit jobs.

---

## Important Notes

- **Always use `--platform linux/amd64`** when building Docker images (Fargate requires x86)
- **Strip whitespace from API keys** - trailing spaces cause "Connection error"
- **Never commit sensitive values** - AWS account IDs, API keys, profiles should use placeholders
- **Terraform state is local** - consider remote state for team usage

## File Reference

| File | Purpose |
|------|---------|
| `SETUP.md` | Step-by-step setup guide |
| `docs/GUIDE.md` | Claude integration and job patterns |
| `infra/terraform/` | Infrastructure as code |
| `control_api/` | FastAPI orchestrator |
| `worker/` | Job processor with Claude support |
| `compose/` | Local development with Docker Compose |
