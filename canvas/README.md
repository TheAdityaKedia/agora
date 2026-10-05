# Canvas API

Backend for **collections** (called canvases in the code): named lists of
events you keep for yourself or share so friends can add, vote and comment. One Python
Lambda behind a Lambda Function URL plus one DynamoDB table, with the
infrastructure defined in code (AWS CDK, Python) in `infra/`. **How it all
works** (features, data model, API, frontend): [`HOW-IT-WORKS.md`](HOW-IT-WORKS.md).
Original design and rationale: [`feature-specs/event-canvases.md`](../feature-specs/event-canvases.md).

This is Agora's only live backend and it's independent of `service/`: no
scraper code or dependencies, and it never touches Neon. The only link to the
rest of Agora is that it reads the published `events.json` to snapshot events.

```
api/handler.py      routes, validation, CORS, rate limits (Function URL payload v2)
api/store.py        DynamoDB single-table access; every write is one transaction
api/snapshot.py     events.json fetch + cache → server-built event snapshots
infra/app.py        CDK app: AgoraCanvasDev, AgoraCanvasProd, AgoraCanvasCi
infra/stacks.py     the stacks: function + URL, table, logs, budget; deploy role
scripts/local_server.py   run the API locally (moto or a real table)
scripts/smoke.py          create → add → vote → read against a deployed URL
scripts/admin.py          operator show / hard-delete a canvas
tests/              pytest + moto (API) and CDK assertions (infra)
```

## Private beta (collections are gated)

Collections ship behind a soft gate: `BETA_GATE = true` in
`frontend/canvas-client.js`. With it on:

- **Invite link:** `https://theadityakedia.github.io/agora/beta/`. Opening it
  makes that browser a **beta member** and lands on the normal site with beta
  **switched on**, where Start a collection / Your collections now appear.
- **The Beta switch.** Members get a "Beta" switch in the header (both
  pages). Beta is on per visit (per tab): a `/beta/` link, `?beta=1` or the
  switch turns it on; opening the plain address in a new tab starts with it
  **off**, so members see the site as everyone else does until they switch
  it on. (Membership is `localStorage` `agora.canvas.beta`; on/off is
  `sessionStorage` `agora.beta.on`.)
- **Shared collections** get links like `…/agora/beta/canvas.html?c=<id>`, so
  whoever opens one is let into the beta too. Without the flag, the site shows
  no collections UI and `canvas.html` says "Collections are in private beta".
- It hides UI only; the API itself is open (nothing secret depends on it).
- The same invite also unlocks the **map view**, gated separately by
  `MAP_BETA_GATE` in `frontend/index.html` (same membership and switch, so
  each feature launches on its own). The SF neighborhood filter was gated
  with it until 2026-10-05; it's now live for everyone.

**To launch:** set `BETA_GATE = false` (one line) and merge. Everyone sees
collections, new links drop `/beta/`, and **keep `frontend/beta/`**: those
pages keep redirecting, so every link shared during the beta still opens.

## Frontend

`frontend/canvas.html` (a canvas, or "Your canvases" without `?c=`) and
canvas mode on `frontend/index.html`, sharing `frontend/canvas-client.js`,
which holds the prod and dev API URLs. Pages served from `localhost` talk to
dev, or to `?api=<url>` (remembered in the browser):

```bash
.venv/bin/python canvas/scripts/local_server.py --moto      # API on :8787
python3 -m http.server -d frontend 8000
# open http://localhost:8000/?api=http://localhost:8787
```

`canvas/scripts/e2e_frontend.py` plays the whole flow in headless Chromium
(two friends: create, add in canvas mode, vote, comment, remove + undo, pick,
custom item) against those two servers and fails on any page error. Needs
`pip install playwright` and a Chromium (`CHROMIUM_PATH=` to reuse one).

## Develop

```bash
python3 -m venv .venv && .venv/bin/pip install -r canvas/requirements-dev.txt
cd canvas && ../.venv/bin/python -m pytest -q
# local API on :8787 with in-memory DynamoDB, snapshots from frontend/events.json
.venv/bin/python canvas/scripts/local_server.py --moto
.venv/bin/python canvas/scripts/smoke.py http://localhost:8787
# see the CloudFormation CDK would deploy (no AWS access needed; needs Node)
cd canvas/infra && PATH=../../.venv/bin:$PATH npx aws-cdk@2.1144.0 synth AgoraCanvasDev
```

## Deploy

Infra changes ship like code changes: edit `infra/stacks.py`, push, and
`.github/workflows/deploy-canvas-api.yml` runs the tests + `cdk synth`, then
`cdk deploy`s — **any branch → `AgoraCanvasDev`** (localhost CORS on,
smoke-tested), **`main` → `AgoraCanvasProd` + `AgoraCanvasCi`** (so even the
deploy role is managed from code). The API URL is in the run's summary.
Nothing is ever uploaded by hand.

GitHub authenticates with OIDC (no stored AWS keys) as
`github-agora-canvas-deploy`, which can only assume the CDK bootstrap roles in
account `978355607698` / `us-east-1` (the Bedrock account; its GitHub OIDC
provider is reused). The role trusts `repo:TheAdityaKedia/agora:ref:refs/heads/*`
— the deploy job declares no GitHub environment, so that's its OIDC subject.
Note the default CDK bootstrap lets CloudFormation act as admin, so anyone who
can push a branch here can change this AWS account through CDK; acceptable for
a single-owner repo, and tightenable later with
`cdk bootstrap --cloudformation-execution-policies <scoped policy ARN>`.

### One-time bootstrap (once per AWS account, needs admin credentials)

CI can't create the role it logs in with, so this runs once by hand:

```bash
python3 -m venv .venv && .venv/bin/pip install -r canvas/infra/requirements.txt
cd canvas/infra
export PATH=../../.venv/bin:$PATH        # admin AWS creds in the env / profile
npx aws-cdk@2.1144.0 bootstrap aws://978355607698/us-east-1
npx aws-cdk@2.1144.0 deploy AgoraCanvasCi
```

Then in GitHub → Settings → Secrets and variables → Actions:
- variable `CANVAS_DEPLOY_ROLE_ARN` =
  `arn:aws:iam::978355607698:role/github-agora-canvas-deploy` (this turns the
  deploy job on)
- variable `CANVAS_BUDGET_EMAIL` = where the $5/month prod budget alert goes
- secret `CANVAS_IP_SALT` = any long random string (`openssl rand -hex 32`)

Re-run the latest "Deploy canvas API" run (or push to `canvas/`). Delete the
admin credentials used above; nothing needs them again.

**Concurrency cap:** off by default. This account's Lambda concurrency limit
is the new-account 10, and AWS rejects any reservation that leaves fewer than
10 unreserved (the first deploy failed exactly that way). The $5 budget alert
is the cost guard meanwhile. To cap a flood: request a "Concurrent
executions" quota increase (Service Quotas → Lambda; usually granted to 1000),
then set repo variable `CANVAS_RESERVED_CONCURRENCY` to e.g. `10`.

## Operate

- **Cost:** Lambda + DynamoDB on-demand at friends-scale is ~$0. Prod has an
  AWS Budget for those two services ($5/month; alert at 80% actual or 100%
  forecast). No concurrency cap until the account quota is raised (see
  Deploy).
- **Logs:** CloudWatch `/aws/lambda/agora-canvas-<stage>`, 14-day retention,
  one line per request (method, route, status, ms). Never log bodies, names,
  client ids or IPs.
- **A reported canvas:** `python canvas/scripts/admin.py --table
  agora-canvas-prod delete <canvasId>` (hard delete, canvas + log).
- **Data is durable:** the prod table has `RemovalPolicy.RETAIN` and prod has
  point-in-time recovery. Deleting the stack leaves the table behind.
