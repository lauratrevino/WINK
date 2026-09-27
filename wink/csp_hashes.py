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


def _js_unescape(text):
    # Inside a JS string literal, \' and \" are resolved before the HTML
    # is ever inserted into the page, so the browser hashes the unescaped
    # form.
    return re.sub(r"\\(['\"\\])", r"\1", text)


def compute_hashes(template_dir, js_dir=None):
    """Hashes every static inline event handler / style attribute found in
    the templates AND (when js_dir is given) in the page scripts. The JS
    files build a lot of HTML at runtime (dashboard study plan, analytics
    tables, chat panels) with style="..." attributes; before js_dir was
    scanned, CSP silently blocked every one of those, so that HTML showed
    up unstyled."""
    script_hashes = set()
    style_hashes = set()
    sources = [(p, False) for p in template_dir.glob("*.html")]
    if js_dir is not None:
        sources += [(p, True) for p in js_dir.glob("*.js") if not p.name.endswith(".min.js")]
    for path, is_js in sources:
        content = path.read_text(encoding="utf-8", errors="ignore")
        variants = (lambda m: {m, _js_unescape(m)}) if is_js else (lambda m: {m})
        for pattern in _EVENT_PATTERNS:
            for m in pattern.findall(content):
                if "${" in m or "{{" in m:
                    continue
                for v in variants(m):
                    script_hashes.add(_hash(v))
        for m in _STYLE_PATTERN.findall(content):
            if "${" in m or "{{" in m:
                continue
            for v in variants(m):
                style_hashes.add(_hash(v))
    return sorted(script_hashes), sorted(style_hashes)
