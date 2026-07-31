"""Tests for the dynamically-computed CSP hash allowlist (csp_hashes.py)."""
import hashlib
import base64
from pathlib import Path

import pytest


def test_no_dynamic_event_or_style_attributes_in_reachable_templates():
    """Every template actually reachable by a real route must have zero
    onclick/style attributes whose content varies per element — those
    can't be hash-allowlisted and would silently stop working the moment
    'unsafe-inline' is removed from script-src-attr/style-src-attr.
    index.html and base.html are excluded deliberately: confirmed dead
    (no route renders either), documented in README.md."""
    import re
    template_dir = Path(__file__).resolve().parent.parent / "templates"
    reachable = [p for p in template_dir.glob("*.html") if p.name not in ("index.html", "base.html")]
    assert reachable, "expected to find the real, routed templates"

    event_attrs = ["onclick", "onkeydown", "onkeyup", "onkeypress", "onchange", "oninput",
                   "onsubmit", "onload", "onerror", "onfocus", "onblur", "onmouseover",
                   "onmouseout", "ondblclick"]
    offenders = []
    for path in reachable:
        content = path.read_text(encoding="utf-8", errors="ignore")
        for attr in event_attrs:
            pattern = re.compile(attr + r'=\\?"((?:[^"\\]|\\.)*)\\?"')
            for m in pattern.findall(content):
                if "${" in m or "{{" in m:
                    offenders.append((path.name, attr, m))
        for m in re.findall(r'style=\\?"((?:[^"\\]|\\.)*)\\?"', content):
            if "${" in m or "{{" in m:
                offenders.append((path.name, "style", m))
    assert not offenders, f"dynamic (un-hashable) attributes found: {offenders}"


def test_computed_hashes_match_known_handler():
    """toggleNav() appears verbatim in several templates — confirms the
    real extraction+hashing pipeline (not just the algorithm in isolation)
    produces the exact hash a browser would compute for real content."""
    from wink.csp_hashes import compute_hashes
    template_dir = Path(__file__).resolve().parent.parent / "templates"
    script_hashes, _ = compute_hashes(template_dir)
    expected = base64.b64encode(hashlib.sha256(b"toggleNav()").digest()).decode()
    assert expected in script_hashes


def test_hash_algorithm_matches_published_csp_example():
    """Verifies the hashing method itself against a known-correct example
    from content-security-policy.com/unsafe-hashes/ — independent of this
    codebase's own templates."""
    from wink.csp_hashes import _hash
    assert _hash("doSomething();") == "RFWPLDbv2BY+rCkDzsE+0fr8ylGr2R2faWMhq4lfEQc="


def test_served_csp_header_contains_computed_hashes(client):
    """End-to-end: the header a real response actually sends contains the
    hash-allowlist mechanism, not just unsafe-inline."""
    resp = client.get("/health")
    csp = resp.headers.get("Content-Security-Policy", "")
    assert "script-src-attr 'unsafe-hashes'" in csp
    assert "style-src-attr 'unsafe-hashes'" in csp
    assert "'unsafe-inline'" not in csp.split("script-src-attr")[1].split(";")[0]

    expected = "sha256-" + base64.b64encode(hashlib.sha256(b"toggleNav()").digest()).decode()
    assert expected in csp
