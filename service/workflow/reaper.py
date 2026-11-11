# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

from typing import Any

from service.common.logs import log

FIXED = {"pk", "slots", "expires_at"}


def handler(event: dict, context: Any = None, *, table: Any = None, states: Any = None) -> dict:
    if table is None or states is None:
        import boto3
        from service.common.aws import config
        from service.common.settings import settings
        s = settings()
        table = table or boto3.resource("dynamodb", region_name=s.region, config=config(2, 3)).Table(s.semaphore_table)
        states = states or boto3.client("stepfunctions", region_name=s.region, config=config(5, 3))
    released = 0
    kw: dict = {}
    while True:
        page = table.scan(**kw)
        for item in page.get("Items", []):
            for attr in [a for a in item if a not in FIXED and a.startswith("arn:")]:
                try:
                    status = states.describe_execution(executionArn=attr)["status"]
                except states.exceptions.ExecutionDoesNotExist:
                    status = "MISSING"
                if status == "RUNNING":
                    continue
                try:
                    table.update_item(Key={"pk": item["pk"]}, UpdateExpression="ADD slots :m REMOVE #e",
                                      ConditionExpression="attribute_exists(#e)",
                                      ExpressionAttributeNames={"#e": attr},
                                      ExpressionAttributeValues={":m": -1})
                    released += 1
                except table.meta.client.exceptions.ConditionalCheckFailedException:
                    pass
        if "LastEvaluatedKey" not in page:
            break
        kw["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    log("reaper", count=released)
    return {"released": released}
