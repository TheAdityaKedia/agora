"""Synth the CDK stacks and assert the properties we rely on."""
import sys
from pathlib import Path

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Match, Template

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "infra"))
from stacks import CanvasApiStack, CiStack  # noqa: E402

ENV = cdk.Environment(account="123456789012", region="us-east-1")


def api_template(stage, **overrides):
    app = cdk.App()
    kwargs = dict(stage=stage, allowed_origins="https://theadityakedia.github.io",
                  ip_hash_salt="salt", manifest_url="https://x/events.json",
                  reserved_concurrency=10, budget_email="me@example.com")
    kwargs.update(overrides)
    return Template.from_stack(CanvasApiStack(app, "T", env=ENV, **kwargs))


@pytest.fixture(scope="module")
def prod():
    return api_template("prod")


@pytest.fixture(scope="module")
def dev():
    return api_template("dev")


def test_table_is_retained_on_demand_with_ttl(prod):
    prod.has_resource("AWS::DynamoDB::Table", {
        "DeletionPolicy": "Retain", "UpdateReplacePolicy": "Retain",
        "Properties": Match.object_like({
            "TableName": "agora-canvas-prod",
            "BillingMode": "PAY_PER_REQUEST",
            "TimeToLiveSpecification": {"AttributeName": "ttl", "Enabled": True},
            "PointInTimeRecoverySpecification": {"PointInTimeRecoveryEnabled": True},
        }),
    })


def test_dev_table_is_disposable(dev):
    # A retained dev table would be orphaned by a rolled-back first deploy and
    # block re-creating the stack (the table name is fixed).
    dev.has_resource("AWS::DynamoDB::Table", {"DeletionPolicy": "Delete"})


def test_dev_has_no_pitr_and_allows_localhost(dev):
    dev.has_resource_properties("AWS::DynamoDB::Table", {
        "PointInTimeRecoverySpecification": {"PointInTimeRecoveryEnabled": False}})
    dev.has_resource_properties("AWS::Lambda::Function", {
        "Environment": {"Variables": Match.object_like({"ALLOW_LOCALHOST": "true"})}})


def test_function_config(prod):
    prod.has_resource_properties("AWS::Lambda::Function", {
        "FunctionName": "agora-canvas-prod",
        "Runtime": "python3.12",
        "Handler": "handler.lambda_handler",
        "ReservedConcurrentExecutions": 10,
        "Environment": {"Variables": Match.object_like({
            "ALLOW_LOCALHOST": "false", "IP_HASH_SALT": "salt",
            "ALLOWED_ORIGINS": "https://theadityakedia.github.io"})},
    })


def test_public_url_with_both_invoke_grants(prod):
    prod.has_resource_properties("AWS::Lambda::Url", {"AuthType": "NONE"})
    prod.has_resource_properties("AWS::Lambda::Permission", {
        "Action": "lambda:InvokeFunctionUrl", "Principal": "*", "FunctionUrlAuthType": "NONE"})
    prod.has_resource_properties("AWS::Lambda::Permission", {
        "Action": "lambda:InvokeFunction", "Principal": "*", "InvokedViaFunctionUrl": True})


def test_logs_kept_two_weeks(prod):
    prod.has_resource_properties("AWS::Logs::LogGroup", {
        "LogGroupName": "/aws/lambda/agora-canvas-prod", "RetentionInDays": 14})


def test_budget_prod_only(prod, dev):
    prod.resource_count_is("AWS::Budgets::Budget", 1)
    dev.resource_count_is("AWS::Budgets::Budget", 0)
    assert api_template("prod", budget_email="").find_resources("AWS::Budgets::Budget") == {}


def test_concurrency_cap_can_be_disabled():
    fn = api_template("dev", reserved_concurrency=0).find_resources("AWS::Lambda::Function")
    assert all("ReservedConcurrentExecutions" not in r["Properties"] for r in fn.values())


def test_deploy_role_trusts_only_this_repo_and_only_assumes_cdk_roles():
    app = cdk.App()
    t = Template.from_stack(CiStack(app, "Ci", env=ENV, github_repo="TheAdityaKedia/agora"))
    t.has_resource_properties("AWS::IAM::Role", {
        "RoleName": "github-agora-canvas-deploy",
        "AssumeRolePolicyDocument": {"Statement": [Match.object_like({
            "Action": "sts:AssumeRoleWithWebIdentity",
            "Condition": {
                "StringEquals": {"token.actions.githubusercontent.com:aud": "sts.amazonaws.com"},
                "StringLike": {"token.actions.githubusercontent.com:sub":
                               "repo:TheAdityaKedia/agora:ref:refs/heads/*"},
            },
        })]},
    })
    t.has_resource_properties("AWS::IAM::Policy", {"PolicyDocument": {"Statement": [{
        "Action": "sts:AssumeRole", "Effect": "Allow",
        "Resource": "arn:aws:iam::123456789012:role/cdk-hnb659fds-*-123456789012-us-east-1",
    }]}})
