import hashlib
import base64
from pathlib import Path



def test_no_dynamic_event_or_style_attributes_in_reachable_templates():
    import re
    template_dir = Path(__file__).resolve().parent.parent / "templates"
    reachable = list(template_dir.glob("*.html"))
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
    from wink.csp_hashes import compute_hashes
    template_dir = Path(__file__).resolve().parent.parent / "templates"
    script_hashes, _ = compute_hashes(template_dir)
    expected = base64.b64encode(hashlib.sha256(b"toggleNav()").digest()).decode()
    assert expected in script_hashes


def test_hash_algorithm_matches_published_csp_example():
    from wink.csp_hashes import _hash
    assert _hash("doSomething();") == "RFWPLDbv2BY+rCkDzsE+0fr8ylGr2R2faWMhq4lfEQc="


def test_hash_of_escaped_apostrophe_matches_the_literal_attribute_text():
    # Regression test: an onclick like setPrompt('I\'m homesick....') was
    # silently broken by CSP for a while because _hash() used to strip the
    # backslash before hashing, computing the hash of a DIFFERENT string
    # than the one the browser actually hashes for a hash-matched inline
    # event handler. The browser only ever HTML-entity-decodes an
    # attribute's value before that check — it never does JS-string
    # backslash-unescaping at that stage — so _hash() must match that
    # exactly: hash the literal text, backslash included.
    from wink.csp_hashes import _hash
    raw = r"setPrompt('I\'m homesick....')"
    assert _hash(raw) == base64.b64encode(hashlib.sha256(raw.encode()).digest()).decode()


def test_escaped_apostrophe_onclick_is_actually_included_in_computed_hashes():
    from wink.csp_hashes import compute_hashes
    template_dir = Path(__file__).resolve().parent.parent / "templates"
    script_hashes, _ = compute_hashes(template_dir)
    raw = r"setPrompt('I\'m homesick....')"
    expected = base64.b64encode(hashlib.sha256(raw.encode()).digest()).decode()
    assert expected in script_hashes, (
        "an onclick handler containing an escaped apostrophe must be hashed "
        "using its literal (backslash-included) text, or the browser will "
        "silently reject it under CSP"
    )


def test_served_csp_header_contains_computed_hashes(client):
    resp = client.get("/health")
    csp = resp.headers.get("Content-Security-Policy", "")
    assert "script-src-attr 'unsafe-hashes'" in csp
    assert "style-src-attr 'unsafe-hashes'" in csp
    assert "'unsafe-inline'" not in csp.split("script-src-attr")[1].split(";")[0]

    expected = "sha256-" + base64.b64encode(hashlib.sha256(b"toggleNav()").digest()).decode()
    assert expected in csp
