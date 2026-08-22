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
    # Only HTML-entity decoding here (&quot;, &#39;, etc.) — that's the one
    # transformation the browser itself applies to an attribute's value
    # before computing its own CSP hash for a hash-matched inline event
    # handler. Backslash-unescaping (\' -> ', \" -> ") used to happen here
    # too, but that's a JS-string-literal concept the browser doesn't apply
    # until it actually parses the code as JavaScript — which happens
    # AFTER the CSP hash check, not before. Stripping those backslashes
    # meant this function computed the hash of a different string than
    # the one the browser hashes for real, so any onclick containing an
    # escaped quote (e.g. setPrompt('...I\'m broke.')) got silently
    # blocked by CSP — the hash here never matched what the browser
    # expected, so the button did nothing when clicked.
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
