"""
Computes the CSP hash-allowlists for inline event handler attributes
(onclick, onkeydown, etc.) and inline style="..." attributes, by scanning
the actual template files at startup — not a hardcoded list. This means
the allowlist can never silently go stale: add or change a static
onclick="..." in any template, restart the app, and the new hash is
picked up automatically. Forget to regenerate a hardcoded list after an
edit and a handler would just silently stop working with no error — this
avoids that failure mode entirely.

Only STATIC attribute values can be hash-allowlisted at all — a value
that varies per element (a per-row document ID, a per-cell heatmap color)
would need a different hash for every possible value, which can't be
enumerated in advance. Every genuinely dynamic onclick/style in this
app's templates was refactored to use a data-* attribute plus a delegated
JavaScript listener (event handlers) or the element.style property
directly (styles — confirmed via MDN that CSP's style-src-attr does not
restrict setting style *properties* via JavaScript, only the style="..."
HTML attribute itself) instead of building the value into markup. What's
left really is fixed, literal text, safe to hash.
"""
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
    """Decodes HTML entities and un-escapes backslash-quotes first — CSP
    hashes the actual text the browser executes/applies, not the raw HTML
    source (which may have entities, or backslash-escaped quotes left over
    from being embedded in a JS template literal)."""
    decoded = html.unescape(text).replace('\\"', '"').replace("\\'", "'")
    return base64.b64encode(hashlib.sha256(decoded.encode()).digest()).decode()


def compute_hashes(template_dir):
    """Returns (script_src_attr_hashes, style_src_attr_hashes) — each a
    sorted list of unique 'sha256-...' values, computed from every
    onclick/style attribute found across every .html file in
    `template_dir`. Any attribute value containing ${ or {{ (a JS
    template-literal interpolation or an un-rendered Jinja expression) is
    skipped rather than hashed — those are dynamic-per-instance and would
    produce a hash that only matches by coincidence; genuinely dynamic
    ones should be refactored to a data-* attribute instead (see module
    docstring), not hashed."""
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
