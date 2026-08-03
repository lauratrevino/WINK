"""
Application factory. `app.py` at the repo root just does:

    from wink import create_app
    app = create_app()

Splitting the old single 2,300-line app.py into this package makes each
concern (auth, documents, chat, calendar, admin, the services they share)
independently readable and editable — a change to how deadlines are
extracted, for instance, now touches one file (services/deadlines.py)
instead of requiring you to scroll through everything else to find it.
"""
import os
import secrets
from datetime import timedelta
from pathlib import Path

from flask import Flask, g, request
from werkzeug.middleware.proxy_fix import ProxyFix

from . import config, csp_hashes, extensions
from .blueprints import admin, auth, calendar, chat, dashboard, documents, grades, misc, research


def create_app():
    app = Flask(
        __name__,
        template_folder=os.path.join(config.BASE_DIR, "templates"),
        static_folder=os.path.join(config.BASE_DIR, "static"),
    )
    app.secret_key = config.SECRET_KEY
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=os.environ.get("FLASK_ENV") != "development",
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(days=7),
        MAX_CONTENT_LENGTH=16 * 1024 * 1024,
    )
    os.makedirs(config.UPLOAD_FOLDER, exist_ok=True)

    # Deployed behind a platform load balancer/reverse proxy (Render, etc.) —
    # without this, request.remote_addr is the proxy's own IP for every
    # visitor (silently making every per-IP rate limit — login, register,
    # forgot-password — apply to all visitors combined instead of each one
    # individually) and url_for(..., _external=True) links (email
    # verification, password reset) can come out as http:// instead of
    # https:// depending on how Flask guesses the scheme. TRUSTED_PROXY_HOPS
    # is the number of reverse proxies between the browser and this app —
    # default 1 covers the common single-load-balancer case; set it via env
    # if your deployment chains more than one.
    trusted_hops = int(os.environ.get("TRUSTED_PROXY_HOPS", "1"))
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=trusted_hops, x_proto=trusted_hops, x_host=trusted_hops)

    extensions.init_app(app)  # CSRF + DB-connection teardown

    # ── CSP nonce plumbing ────────────────────────────────────
    # Generates a real per-request nonce and exposes it to templates as
    # csp_nonce(). Every template's inline <script>/<style> tag now carries
    # nonce="{{ csp_nonce() }}" (see templates/), and the header below uses
    # the CSP3 script-src-elem/style-src-elem directives to require it —
    # this blocks an attacker-injected <script>/<style> element from
    # executing even if they find an injection point.
    @app.before_request
    def _set_csp_nonce():
        g.csp_nonce = secrets.token_urlsafe(16)

    app.jinja_env.globals["csp_nonce"] = lambda: g.csp_nonce

    # ── CSP hash allowlists for event handlers and style attributes ──
    # Computed once, here, directly from the real template files — not a
    # hardcoded list that could silently drift out of sync after a future
    # template edit. See csp_hashes.py for what is and isn't hashable (a
    # dynamic per-instance value like a document ID can't be — those were
    # all refactored to data-* attributes plus a delegated listener or a
    # direct .style property assignment instead of appearing in markup).
    _script_hashes, _style_hashes = csp_hashes.compute_hashes(Path(config.BASE_DIR) / "templates")
    _script_src_attr = " ".join(f"'sha256-{h}'" for h in _script_hashes)
    _style_src_attr = " ".join(f"'sha256-{h}'" for h in _style_hashes)
    print(f"CSP: {len(_script_hashes)} event-handler hashes, {len(_style_hashes)} style-attribute hashes computed from templates/")

    @app.after_request
    def set_security_headers(response):
        nonce = g.get("csp_nonce", "")
        # script-src/style-src (bare) stay as a fallback for browsers that
        # predate the CSP3 script-src-elem/-attr split. Browsers that
        # understand nonces and hashes also understand that split (they
        # shipped together), so this doesn't reopen the hole for anyone
        # modern:
        # - script-src-elem / style-src-elem: only <script>/<style> tags
        #   carrying this request's nonce may run — blocks an
        #   attacker-injected <script src="https://evil.com/x.js"> or
        #   <style> tag from ever executing, the highest-value XSS payload.
        # - script-src-attr / style-src-attr: only inline event handlers
        #   (onclick=...) and style="..." attributes whose exact, known
        #   content matches one of the hashes computed above may run —
        #   blocks an attacker-injected onclick/style whose content doesn't
        #   match anything already in the app, while every existing handler
        #   keeps working unchanged.
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            f"script-src-elem 'self' 'nonce-{nonce}'; "
            f"script-src-attr 'unsafe-hashes' {_script_src_attr}; "
            "style-src 'self' 'unsafe-inline'; "
            f"style-src-elem 'self' 'nonce-{nonce}'; "
            f"style-src-attr 'unsafe-hashes' {_style_src_attr}; "
            "img-src 'self' https: data:; "
            "frame-src https://www.google.com; "
            "connect-src 'self'; "
            "object-src 'none'; "
            "base-uri 'self'; "
            "frame-ancestors 'self';"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        # Static assets (nav.css, csrf-fetch.js, images) are shared,
        # unchanging-between-deploys files now that they're extracted out of
        # the templates — cache them client-side so a returning visitor's
        # browser skips re-downloading them entirely instead of re-fetching
        # the same bytes on every single page load. This is the payoff of
        # the CSS/JS/image extraction: at low traffic it saves a little
        # bandwidth; at thousands of concurrent students it's the difference
        # between the server answering a full request or a free 304.
        # STATIC_CACHE_MAX_AGE_SECONDS is deliberately moderate (a day, not
        # a year) since these files have no cache-busting/version-hash
        # scheme yet — a shorter cache means an edit to nav.css reaches
        # everyone within a day rather than being stuck behind a year-long
        # cache on browsers that already fetched the old version. If/when
        # static filenames get content-hashed (e.g. nav.a1b2c3.css), this can
        # switch to a long "immutable" cache safely.
        if request.path.startswith("/static/"):
            response.headers["Cache-Control"] = f"public, max-age={config.STATIC_CACHE_MAX_AGE_SECONDS}"
        # Tells the browser to force HTTPS on every future visit for a year,
        # so a visitor who reaches the app over plain HTTP even once (a typo,
        # an old bookmark) doesn't stay downgradeable after that. Gated the
        # same way SESSION_COOKIE_SECURE is above — skipped in local dev
        # (FLASK_ENV=development), which usually runs over plain HTTP with no
        # TLS at all, where this header would be actively unhelpful.
        if os.environ.get("FLASK_ENV") != "development":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    for bp_module in (misc, auth, dashboard, documents, calendar, chat, admin, research, grades):
        app.register_blueprint(bp_module.bp)

    with app.app_context():
        extensions.init_db()

    return app
