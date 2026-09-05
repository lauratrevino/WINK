import base64
import hashlib
import html
import re

EVENT_ATTRS = [
    "onclick", "onkeydown", "onkeyup", "onkeypress", "onchange", "oninput",
    "onsubmit", "onload", "onerror", "onfocus", "onblur", "onmouseover",
    "onmouseout", "ondblclick",
]

_EVENT_PATTERNS = [re.compile(attr + r'=\\?"((?:[^"\\]|\\.)*)\\?"') for attr in EVENT_ATTRS]
_STYLE_PATTERN = re.compile(r'style=\\?"((?:[^"\\]|\\.)*)\\?"')


def _hash(text):
    # Only HTML-entity decoding (&quot;, &#39;, etc.) — that's the one
    # transformation the browser applies to an attribute's value before
    # computing its own CSP hash for a hash-matched inline event handler.
    # Backslash escapes (\', \") are a JS-string-literal concept the
    # browser only resolves once it parses the attribute as JavaScript,
    # which happens after the CSP hash check — so they must be left
    # exactly as written here, or the computed hash won't match what the
    # browser expects and the handler gets silently blocked.
    decoded = html.unescape(text)
    return base64.b64encode(hashlib.sha256(decoded.encode()).digest()).decode()


def compute_hashes(template_dir):
    script_hashes = set()
    style_hashes = set()
    for path in template_dir.glob("*.html"):
        content = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in _EVENT_PATTERNS:
            for m in pattern.findall(content):
                if "${" in m or "{{" in m:
                    continue
                script_hashes.add(_hash(m))
        for m in _STYLE_PATTERN.findall(content):
            if "${" in m or "{{" in m:
                continue
            style_hashes.add(_hash(m))
    return sorted(script_hashes), sorted(style_hashes)
