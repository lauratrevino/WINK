import json
import re


def strip_json_fence(raw):
    m = re.search(r"```(?:json)?\s*(.*?)\s*```", raw, re.DOTALL)
    if m:
        return m.group(1).strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1] if "\n" in raw else raw
        raw = raw.rstrip("`").rstrip()
    return raw


def parse_json_array(raw):
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    last_brace = raw.rfind("}")
    if last_brace == -1:
        return []
    try:
        return json.loads(raw[:last_brace + 1] + "]")
    except json.JSONDecodeError:
        return []
