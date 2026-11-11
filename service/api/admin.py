# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from service.common.aws import valid_namespace
from service.common.http import HttpError, error_response, json_body, response
from service.common.logs import log
from service.common.schema import schema_text, validate

DEFAULT_LIMITS = {"rate": 20.0, "burst": 40, "quota": 100000, "max_concurrent_jobs": 10}
SCOPES = ("classify:write", "jobs:read", "labels:write")


@dataclass
class AdminClients:
    consumers: Any
    cognito: Any
    apigateway: Any
    appconfig: Any
    cloudwatch: Any
    settings: Any


def default_clients() -> AdminClients:
    import boto3
    from service.common.aws import config
    from service.common.settings import settings
    s = settings()
    c = config(10, 3)
    return AdminClients(
        consumers=boto3.resource("dynamodb", region_name=s.region, config=c).Table(s.consumers_table),
        cognito=boto3.client("cognito-idp", region_name=s.region, config=c),
        apigateway=boto3.client("apigateway", region_name=s.region, config=c),
        appconfig=boto3.client("appconfig", region_name=s.region, config=c),
        cloudwatch=boto3.client("cloudwatch", region_name=s.region, config=c),
        settings=s,
    )


def default_config(namespace: str, classes: list[str]) -> dict:
    return {
        "config_version": f"{namespace}-initial",
        "taxonomy_version": "tx-1",
        "classes": classes,
        "tiers": [
            {"name": "titan_nn", "timeout_ms": 3000, "thresholds": {"tau": 0.9, "delta": 0.1}},
        ],
        "unit_prices": {},
    }


def _limits(body: dict) -> dict:
    out = dict(DEFAULT_LIMITS)
    for k in out:
        if k in body:
            v = body[k]
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
                raise HttpError(400, "invalid_limit", field=k)
            out[k] = v
    return out


def _alarm(c: AdminClients, ns: str) -> str:
    s = c.settings
    name = f"{s.metric_namespace}-{ns}-unknown-rate"
    kw = dict(AlarmName=name, Namespace=s.metric_namespace, MetricName="UnknownRate",
              Dimensions=[{"Name": "namespace", "Value": ns}], Statistic="Average", Period=300,
              EvaluationPeriods=3, DatapointsToAlarm=2, Threshold=0.5, ComparisonOperator="GreaterThanThreshold",
              TreatMissingData="notBreaching")
    if s.alarm_topic_arn:
        kw["AlarmActions"] = [s.alarm_topic_arn]
    c.cloudwatch.put_metric_alarm(**kw)
    # Start the new alarm in OK so the first AppConfig deployment can proceed.
    c.cloudwatch.set_alarm_state(AlarmName=name, StateValue="OK", StateReason="new namespace, no data yet")
    alarms = c.cloudwatch.describe_alarms(AlarmNames=[name])["MetricAlarms"]
    return alarms[0]["AlarmArn"]


def _appconfig(c: AdminClients, ns: str, alarm_arn: str, cfg: dict) -> dict:
    s = c.settings
    app = s.appconfig_application
    profile = c.appconfig.create_configuration_profile(
        ApplicationId=app, Name=ns, LocationUri="hosted", Type="AWS.Freeform",
        Validators=[{"Type": "JSON_SCHEMA", "Content": schema_text()}])
    monitors = [{"AlarmArn": alarm_arn}]
    if s.appconfig_monitor_role_arn:
        monitors[0]["AlarmRoleArn"] = s.appconfig_monitor_role_arn
    env = c.appconfig.create_environment(ApplicationId=app, Name=ns, Monitors=monitors)
    version = c.appconfig.create_hosted_configuration_version(
        ApplicationId=app, ConfigurationProfileId=profile["Id"], Content=json.dumps(cfg).encode("utf-8"),
        ContentType="application/json")
    c.appconfig.start_deployment(ApplicationId=app, EnvironmentId=env["Id"], DeploymentStrategyId=s.appconfig_deployment_strategy,
                                 ConfigurationProfileId=profile["Id"], ConfigurationVersion=str(version["VersionNumber"]))
    return {"appconfig_profile_id": profile["Id"], "appconfig_environment_id": env["Id"]}


def onboard(body: dict, c: AdminClients) -> tuple[int, dict]:
    name = body.get("name")
    if not isinstance(name, str) or not valid_namespace(name):
        raise HttpError(400, "invalid_name", pattern="^[a-z][a-z0-9-]{1,30}$")
    classes = body.get("classes")
    if not isinstance(classes, list) or not classes or not all(isinstance(x, str) and x for x in classes):
        raise HttpError(400, "classes_required")
    cfg = default_config(name, classes)
    problems = validate(cfg)
    if problems:
        raise HttpError(400, "invalid_classes", problems=problems[:5])
    existing = c.consumers.get_item(Key={"pk": f"ns#{name}#consumer"}).get("Item")
    if existing and existing.get("state") == "complete":
        return 200, {"namespace": name, "client_id": existing["client_id"], "api_key_id": existing["api_key_id"],
                     "usage_plan_id": existing["usage_plan_id"], "created": False}
    limits = _limits(body)
    s = c.settings
    rec: dict[str, Any] = dict(existing or {"pk": f"ns#{name}#consumer", "namespace": name, "state": "creating",
                                             "created_at": int(time.time())})
    out: dict[str, Any] = {"namespace": name, "created": True}
    if "client_id" not in rec:
        client = c.cognito.create_user_pool_client(
            UserPoolId=s.user_pool_id, ClientName=name, GenerateSecret=True,
            AllowedOAuthFlows=["client_credentials"], AllowedOAuthFlowsUserPoolClient=True,
            AllowedOAuthScopes=[f"{s.resource_server}/{x}" for x in SCOPES],
            ExplicitAuthFlows=["ALLOW_REFRESH_TOKEN_AUTH"])["UserPoolClient"]
        rec["client_id"] = client["ClientId"]
        out["client_secret"] = client.get("ClientSecret")
        c.consumers.put_item(Item=rec)
    if "api_key_id" not in rec:
        key = c.apigateway.create_api_key(name=f"docclass-{name}", enabled=True)
        rec["api_key_id"] = key["id"]
        out["api_key"] = key.get("value")
        c.consumers.put_item(Item=rec)
    if "usage_plan_id" not in rec:
        plan = c.apigateway.create_usage_plan(
            name=f"docclass-{name}", apiStages=[{"apiId": s.api_id, "stage": s.api_stage}],
            throttle={"rateLimit": float(limits["rate"]), "burstLimit": int(limits["burst"])},
            quota={"limit": int(limits["quota"]), "period": "MONTH"})
        c.apigateway.create_usage_plan_key(usagePlanId=plan["id"], keyId=rec["api_key_id"], keyType="API_KEY")
        rec["usage_plan_id"] = plan["id"]
        c.consumers.put_item(Item=rec)
    if "alarm_arn" not in rec:
        rec["alarm_arn"] = _alarm(c, name)
        c.consumers.put_item(Item=rec)
    if "appconfig_profile_id" not in rec:
        rec.update(_appconfig(c, name, rec["alarm_arn"], cfg))
        c.consumers.put_item(Item=rec)
    rec["max_concurrent_jobs"] = int(limits["max_concurrent_jobs"])
    rec["state"] = "complete"
    c.consumers.put_item(Item=rec)
    c.consumers.put_item(Item={"pk": f"client#{rec['client_id']}", "namespace": name, "api_key_id": rec["api_key_id"]})
    for arn in body.get("iam_principals") or []:
        c.consumers.put_item(Item={"pk": f"iam#{arn}", "namespace": name})
    log("consumer_onboarded", namespace=name)
    out.update({"client_id": rec["client_id"], "api_key_id": rec["api_key_id"], "usage_plan_id": rec["usage_plan_id"]})
    return 201, out


def handler(event: dict, context: Any = None, clients: AdminClients | None = None) -> dict:
    try:
        if (event.get("requestContext") or {}).get("identity", {}).get("userArn") is None:
            raise HttpError(403, "iam_auth_required")
        status, body = onboard(json_body(event), clients or default_clients())
        return response(status, body)
    except HttpError as e:
        return error_response(e)
