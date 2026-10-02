# Canvas API

Backend for **event canvases** (shareable hangout shortlists). One Python
Lambda behind a Lambda Function URL plus one DynamoDB table, defined in
`template.yaml` (AWS SAM). Design and rationale:
[`feature-specs/event-canvases.md`](../feature-specs/event-canvases.md).

This is Agora's only live backend and it's independent of `service/`: no
scraper code or dependencies, and it never touches Neon. The only link to the
rest of Agora is that it reads the published `events.json` to snapshot events.

```
api/handler.py      routes, validation, CORS, rate limits (Function URL payload v2)
api/store.py        DynamoDB single-table access; every write is one transaction
api/snapshot.py     events.json fetch + cache → server-built event snapshots
template.yaml       the stack: function + URL, table, log group, budget
bootstrap.yaml      one-time: deploy role (GitHub OIDC) + artifact bucket
scripts/local_server.py   run the API locally (moto or a real table)
scripts/smoke.py          create → add → vote → read against a deployed URL
scripts/admin.py          operator show / hard-delete a canvas
tests/              pytest + moto
```

## Develop

```bash
python3 -m venv .venv && .venv/bin/pip install -r canvas/requirements-dev.txt
cd canvas && ../.venv/bin/python -m pytest -q
# local API on :8787 with in-memory DynamoDB, snapshots from frontend/events.json
.venv/bin/python canvas/scripts/local_server.py --moto
.venv/bin/python canvas/scripts/smoke.py http://localhost:8787
```

## Deploy

`.github/workflows/deploy-canvas-api.yml` runs the tests on every push that
touches `canvas/`, then `sam deploy`s: **any branch → `agora-canvas-dev`**
(localhost CORS on, smoke-tested), **`main` → `agora-canvas-prod`**. The
deploy job is skipped until the one-time setup below is done. The API URL is
printed in the run's summary.

### One-time AWS setup (owner, ~5 min)

Uses the AWS account (`978355607698`) and region (`us-east-1`) the Bedrock
role already uses; that role's GitHub OIDC provider is reused. The deploy
role trusts `repo:TheAdityaKedia/agora:ref:refs/heads/*` (the deploy job
declares no GitHub environment, so that's its OIDC subject).

1. Deploy the bootstrap stack as an admin. Easiest, no local creds: AWS
   console → CloudFormation (us-east-1) → Create stack → *Upload a template
   file* → `canvas/bootstrap.yaml` → name `agora-canvas-bootstrap` → tick the
   IAM acknowledgement → Submit; the values are on the **Outputs** tab. Or,
   with admin CLI credentials:
   ```bash
   aws cloudformation deploy --region us-east-1 \
     --template-file canvas/bootstrap.yaml --stack-name agora-canvas-bootstrap \
     --capabilities CAPABILITY_NAMED_IAM
   aws cloudformation describe-stacks --region us-east-1 \
     --stack-name agora-canvas-bootstrap --query 'Stacks[0].Outputs'
   ```
2. In GitHub → Settings → Secrets and variables → Actions:
   - variable `CANVAS_DEPLOY_ROLE_ARN` = the `DeployRoleArn` output
   - variable `CANVAS_ARTIFACT_BUCKET` = the `ArtifactBucket` output
   - variable `CANVAS_BUDGET_EMAIL` = where the $5/month budget alert goes
   - secret `CANVAS_IP_SALT` = any long random string (`openssl rand -hex 32`)
   - variable `CANVAS_AWS_REGION` only if not `us-east-1` (the default; the
     existing `AWS_REGION` secret is `production`-environment-only, so the
     deploy job can't read it)
3. Re-run the latest "Deploy canvas API" workflow (or push to `canvas/`).

If the deploy fails with *"Specified ReservedConcurrentExecutions … decreases
account's UnreservedConcurrentExecution below its minimum"*, the account's
concurrency limit is the new-account default of 10: set repo variable
`CANVAS_RESERVED_CONCURRENCY` to `0` (no cap) and re-run.

## Operate

- **Cost:** Lambda + DynamoDB on-demand at friends-scale is ~$0. Prod has an
  AWS Budget for those two services ($5/month; alert at 80% actual or 100%
  forecast) and reserved concurrency 10 as a flood cap.
- **Logs:** CloudWatch `/aws/lambda/agora-canvas-<stage>`, 14-day retention,
  one line per request (method, route, status, ms). Never log bodies, names,
  client ids or IPs.
- **A reported canvas:** `python canvas/scripts/admin.py --table
  agora-canvas-prod delete <canvasId>` (hard delete, canvas + log).
- **Data is durable:** the table has `DeletionPolicy: Retain` and prod has
  point-in-time recovery. Deleting the stack leaves the table behind.
