#!/usr/bin/env python3
"""CDK app for the canvas API. Run from canvas/infra:

    npx aws-cdk@2 synth                       # all stacks, no AWS access needed
    npx aws-cdk@2 deploy AgoraCanvasDev       # what CI does on a branch push

Settings come from the environment so CI can inject secrets:
CANVAS_IP_SALT, CANVAS_BUDGET_EMAIL, CANVAS_RESERVED_CONCURRENCY (default 10),
CDK_DEFAULT_ACCOUNT / CDK_DEFAULT_REGION (set by the CDK CLI from your creds).
"""
import os

import aws_cdk as cdk

from stacks import CanvasApiStack, CiStack

ACCOUNT = "978355607698"  # the account the Bedrock role already lives in
REGION = "us-east-1"
GITHUB_REPO = "TheAdityaKedia/agora"
ORIGINS = "https://theadityakedia.github.io"
MANIFEST_URL = "https://theadityakedia.github.io/agora/events.json"

app = cdk.App()
env = cdk.Environment(account=ACCOUNT, region=REGION)
for stage in ("dev", "prod"):
    CanvasApiStack(
        app, f"AgoraCanvas{stage.title()}", env=env, stage=stage,
        allowed_origins=ORIGINS,
        ip_hash_salt=os.environ.get("CANVAS_IP_SALT", ""),
        manifest_url=MANIFEST_URL,
        reserved_concurrency=int(os.environ.get("CANVAS_RESERVED_CONCURRENCY") or 10),
        budget_email=os.environ.get("CANVAS_BUDGET_EMAIL", ""),
        description=f"Agora event canvases API ({stage})",
    )
CiStack(app, "AgoraCanvasCi", env=env, github_repo=GITHUB_REPO,
        description="Role GitHub Actions assumes to deploy the Agora canvas API")
cdk.Tags.of(app).add("project", "agora-canvas")
app.synth()
