
PRICING_PER_MILLION_TOKENS = {
    "claude-haiku-4-5-20251001": {"input": 1.00, "output": 5.00, "cache_write": 1.25, "cache_read": 0.10},
    "claude-sonnet-5":           {"input": 2.00, "output": 10.00, "cache_write": 2.50, "cache_read": 0.20},
    "claude-sonnet-4-6":         {"input": 3.00, "output": 15.00, "cache_write": 3.75, "cache_read": 0.30},
    "claude-opus-5":             {"input": 5.00, "output": 25.00, "cache_write": 6.25, "cache_read": 0.50},
    "claude-opus-4-8":           {"input": 5.00, "output": 25.00, "cache_write": 6.25, "cache_read": 0.50},
    "claude-fable-5":            {"input": 10.00, "output": 50.00, "cache_write": 12.50, "cache_read": 1.00},
    "claude-mythos-5":           {"input": 10.00, "output": 50.00, "cache_write": 12.50, "cache_read": 1.00},
}
_FALLBACK_MODEL = "claude-haiku-4-5-20251001"


def estimate_cost_usd(model, input_tokens=0, output_tokens=0,
                      cache_creation_input_tokens=0, cache_read_input_tokens=0):
    rates = PRICING_PER_MILLION_TOKENS.get(model, PRICING_PER_MILLION_TOKENS[_FALLBACK_MODEL])
    cost = (
        (input_tokens or 0) * rates["input"]
        + (output_tokens or 0) * rates["output"]
        + (cache_creation_input_tokens or 0) * rates["cache_write"]
        + (cache_read_input_tokens or 0) * rates["cache_read"]
    ) / 1_000_000
    return round(cost, 6)
