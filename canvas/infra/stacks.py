"""CDK stacks for the event canvases API. Design: feature-specs/event-canvases.md.

    AgoraCanvasDev / AgoraCanvasProd   CanvasApiStack: Lambda + Function URL,
                                        DynamoDB table, log group, (prod) budget
    AgoraCanvasCi                      CiStack: the role GitHub Actions assumes
                                        (OIDC) to run `cdk deploy`

Everything here deploys from `.github/workflows/deploy-canvas-api.yml` on push
(branch → Dev, main → Prod + Ci); the only manual step ever is the first
`cdk bootstrap` + `cdk deploy AgoraCanvasCi` (canvas/README.md).
"""
from pathlib import Path

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    aws_budgets as budgets,
    aws_dynamodb as dynamodb,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_logs as logs,
)
from constructs import Construct

API_DIR = Path(__file__).resolve().parents[1] / "api"
GITHUB_OIDC_HOST = "token.actions.githubusercontent.com"
DEPLOY_ROLE_NAME = "github-agora-canvas-deploy"


class CanvasApiStack(Stack):
    def __init__(self, scope: Construct, id: str, *, stage: str, allowed_origins: str,
                 ip_hash_salt: str, manifest_url: str, reserved_concurrency: int,
                 budget_email: str, **kwargs):
        super().__init__(scope, id, **kwargs)
        prod = stage == "prod"
        name = f"agora-canvas-{stage}"

        table = dynamodb.Table(
            self, "Table",
            table_name=name,
            partition_key=dynamodb.Attribute(name="PK", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="SK", type=dynamodb.AttributeType.STRING),
            # On-demand: a consistent read of a big canvas would throttle the
            # free-tier provisioned capacity; on-demand at this scale is pennies.
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            time_to_live_attribute="ttl",
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=prod),
            # Prod canvases are kept forever: no stack change may drop the table.
            # Dev data is disposable, and retaining it would orphan the table
            # (blocking re-creation by name) whenever a first deploy rolls back.
            removal_policy=RemovalPolicy.RETAIN if prod else RemovalPolicy.DESTROY,
        )

        log_group = logs.LogGroup(
            self, "Logs",
            log_group_name=f"/aws/lambda/{name}",
            retention=logs.RetentionDays.TWO_WEEKS,
            removal_policy=RemovalPolicy.DESTROY,
        )

        fn = lambda_.Function(
            self, "Function",
            function_name=name,
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.ARM_64,
            handler="handler.lambda_handler",
            # Stdlib + boto3 (in the runtime) only, so no bundling step.
            code=lambda_.Code.from_asset(str(API_DIR), exclude=["__pycache__", "*.pyc"]),
            memory_size=512,  # the ~7 MB manifest parse is the heaviest step
            timeout=Duration.seconds(15),
            reserved_concurrent_executions=reserved_concurrency or None,
            log_group=log_group,
            environment={
                "TABLE_NAME": table.table_name,
                "ALLOWED_ORIGINS": allowed_origins,
                "ALLOW_LOCALHOST": "false" if prod else "true",
                "IP_HASH_SALT": ip_hash_salt,
                "MANIFEST_URL": manifest_url,
            },
        )
        table.grant_read_write_data(fn)

        # Public URL; CORS is handled in handler.py. CDK adds both grants AWS
        # requires (InvokeFunctionUrl + InvokeFunction via the URL only).
        url = fn.add_function_url(auth_type=lambda_.FunctionUrlAuthType.NONE)

        if prod and budget_email:
            subscribers = [budgets.CfnBudget.SubscriberProperty(
                subscription_type="EMAIL", address=budget_email)]
            budgets.CfnBudget(
                self, "Budget",
                budget=budgets.CfnBudget.BudgetDataProperty(
                    budget_name="agora-canvas-monthly",
                    budget_type="COST",
                    time_unit="MONTHLY",
                    budget_limit=budgets.CfnBudget.SpendProperty(amount=5, unit="USD"),
                    cost_filters={"Service": ["AWS Lambda", "Amazon DynamoDB"]},
                ),
                notifications_with_subscribers=[
                    budgets.CfnBudget.NotificationWithSubscribersProperty(
                        notification=budgets.CfnBudget.NotificationProperty(
                            notification_type=kind, comparison_operator="GREATER_THAN",
                            threshold=threshold),
                        subscribers=subscribers)
                    for kind, threshold in (("ACTUAL", 80), ("FORECASTED", 100))
                ],
            )

        CfnOutput(self, "ApiUrl", value=url.url, description="Canvas API base URL")
        CfnOutput(self, "TableName", value=table.table_name)


class CiStack(Stack):
    """The role GitHub Actions assumes to deploy. It holds no AWS permissions of
    its own beyond assuming the CDK bootstrap roles, which do the work."""

    def __init__(self, scope: Construct, id: str, *, github_repo: str, **kwargs):
        super().__init__(scope, id, **kwargs)
        # The account already has GitHub's OIDC provider (the Bedrock role uses it).
        provider_arn = f"arn:aws:iam::{self.account}:oidc-provider/{GITHUB_OIDC_HOST}"
        role = iam.Role(
            self, "DeployRole",
            role_name=DEPLOY_ROLE_NAME,
            max_session_duration=Duration.hours(1),
            assumed_by=iam.WebIdentityPrincipal(provider_arn, conditions={
                "StringEquals": {f"{GITHUB_OIDC_HOST}:aud": "sts.amazonaws.com"},
                # Branch pushes only (the deploy job declares no environment).
                "StringLike": {f"{GITHUB_OIDC_HOST}:sub": f"repo:{github_repo}:ref:refs/heads/*"},
            }),
        )
        role.add_to_policy(iam.PolicyStatement(
            actions=["sts:AssumeRole"],
            resources=[f"arn:aws:iam::{self.account}:role/cdk-hnb659fds-*-{self.account}-{self.region}"],
        ))
        CfnOutput(self, "DeployRoleArn", value=role.role_arn)
