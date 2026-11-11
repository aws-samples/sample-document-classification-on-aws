# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

class DocClassError(Exception):
    pass


class InvalidDecisionError(DocClassError):
    pass


class ConfigError(DocClassError):
    pass


class TierTimeout(DocClassError):
    pass
