# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Remove what onboarding created for a namespace, outside Terraform.

    python3 scripts/remove_consumer.py --namespace team-a            # dry run
    python3 scripts/remove_consumer.py --namespace team-a --apply

Removes the usage plan and API key, the Cognito app client, the UnknownRate
alarm, the AppConfig environment, profile and hosted versions, and the
namespace's items in the consumers table. Documents, job records and models
are left alone.

AppConfig refuses to delete an environment or profile that was used in the
last 60 minutes (deletion protection). Wait, or pass
--bypass-deletion-protection to override it for this namespace only.
"""

from __future__ import annotations

import argparse
import sys
import time

import boto3
from boto3.dynamodb.conditions import Attr


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--namespace", required=True)
    ap.add_argument("--name", default="docclass", help="the module's name input")
    ap.add_argument("--region")
    ap.add_argument("--apply", action="store_true", help="delete; without it, only list what would be deleted")
    ap.add_argument("--bypass-deletion-protection", action="store_true")
    args = ap.parse_args()
    ns = args.namespace

    s = boto3.Session(region_name=args.region)
    apigw, cog, cw, ac = s.client("apigateway"), s.client("cognito-idp"), s.client("cloudwatch"), s.client("appconfig")
    table = s.resource("dynamodb").Table(f"{args.name}-consumers")
    pool = next(p["Id"] for p in cog.list_user_pools(MaxResults=60)["UserPools"] if p["Name"] == f"{args.name}-consumers")
    app = next(a["Id"] for a in ac.list_applications()["Items"] if a["Name"] == args.name)
    bypass = {"DeletionProtectionCheck": "BYPASS"} if args.bypass_deletion_protection else {}
    found = 0

    def do(desc: str, fn) -> None:
        nonlocal found
        found += 1
        print(("delete " if args.apply else "would delete ") + desc, flush=True)
        if args.apply:
            fn()

    for plan in [p for p in apigw.get_usage_plans(limit=500)["items"] if p["name"] == f"docclass-{ns}"]:
        for k in apigw.get_usage_plan_keys(usagePlanId=plan["id"]).get("items", []):
            do(f"usage plan key {k['id']}",
               lambda p=plan, k=k: apigw.delete_usage_plan_key(usagePlanId=p["id"], keyId=k["id"]))

        def delete_plan(p=plan) -> None:
            stages = [f"{st['apiId']}:{st['stage']}" for st in p.get("apiStages", [])]
            if stages:
                apigw.update_usage_plan(usagePlanId=p["id"], patchOperations=[
                    {"op": "remove", "path": "/apiStages", "value": v} for v in stages])
            apigw.delete_usage_plan(usagePlanId=p["id"])
        do(f"usage plan {plan['id']} ({plan['name']})", delete_plan)
    for key in [k for k in apigw.get_api_keys(limit=500, nameQuery=f"docclass-{ns}")["items"]
                if k["name"] == f"docclass-{ns}"]:
        do(f"API key {key['id']}", lambda k=key: apigw.delete_api_key(apiKey=k["id"]))
    for page in cog.get_paginator("list_user_pool_clients").paginate(UserPoolId=pool, MaxResults=60):
        for c in [c for c in page["UserPoolClients"] if c["ClientName"] == ns]:
            do(f"Cognito app client {c['ClientId']}",
               lambda c=c: cog.delete_user_pool_client(UserPoolId=pool, ClientId=c["ClientId"]))
    alarm = f"DocClass-{ns}-unknown-rate"
    if cw.describe_alarms(AlarmNames=[alarm])["MetricAlarms"]:
        do(f"alarm {alarm}", lambda: cw.delete_alarms(AlarmNames=[alarm]))
    for env in [e for e in ac.list_environments(ApplicationId=app)["Items"] if e["Name"] == ns]:
        def delete_env(e=env) -> None:
            for d in ac.list_deployments(ApplicationId=app, EnvironmentId=e["Id"])["Items"]:
                if d["State"] in ("DEPLOYING", "BAKING", "VALIDATING"):
                    ac.stop_deployment(ApplicationId=app, EnvironmentId=e["Id"], DeploymentNumber=d["DeploymentNumber"])
            for _ in range(30):
                try:
                    return ac.delete_environment(ApplicationId=app, EnvironmentId=e["Id"], **bypass)
                except ac.exceptions.ConflictException:
                    time.sleep(5)
            raise RuntimeError("the environment is still busy")
        do(f"AppConfig environment {ns}", delete_env)
    for prof in [p for p in ac.list_configuration_profiles(ApplicationId=app)["Items"] if p["Name"] == ns]:
        def delete_profile(p=prof) -> None:
            for v in ac.list_hosted_configuration_versions(ApplicationId=app, ConfigurationProfileId=p["Id"])["Items"]:
                ac.delete_hosted_configuration_version(ApplicationId=app, ConfigurationProfileId=p["Id"],
                                                       VersionNumber=v["VersionNumber"])
            ac.delete_configuration_profile(ApplicationId=app, ConfigurationProfileId=p["Id"], **bypass)
        do(f"AppConfig profile {ns} and its hosted versions", delete_profile)
    scan = table.scan(FilterExpression=Attr("namespace").eq(ns), ProjectionExpression="pk")
    for pk in sorted(i["pk"] for i in scan["Items"]):
        do(f"consumers item {pk}", lambda pk=pk: table.delete_item(Key={"pk": pk}))

    if not found:
        print(f"nothing found for namespace {ns}")
    elif not args.apply:
        print("dry run: pass --apply to delete")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        if "actively being used" in str(e) or "deletion protection" in str(e).lower():
            sys.exit(f"{e}\nAppConfig deletion protection: wait an hour after the namespace was last used, "
                     "or re-run with --bypass-deletion-protection.")
        raise
