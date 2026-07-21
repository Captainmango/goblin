from __future__ import annotations

import json

import click


class JsonParamType(click.ParamType):
    name = "json"

    def convert(self, value, param, ctx):
        try:
            return json.loads(value)
        except json.JSONDecodeError as e:
            self.fail(f"Invalid JSON: {e}", param, ctx)


JSON = JsonParamType()
