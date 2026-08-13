import logging
import os
import secrets
from datetime import timedelta
from pathlib import Path

from flask import Flask, g, request
from werkzeug.middleware.proxy_fix import ProxyFix

from . import config, csp_hashes, extensions
from .blueprints import admin, auth, calendar, chat, dashboard, demo, documents, grades, misc, progress, research, webhooks

logger = logging.getLogger(__name__)


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

    trusted_hops = int(os.environ.get("TRUSTED_PROXY_HOPS", "1"))
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=trusted_hops, x_proto=trusted_hops, x_host=trusted_hops)

    extensions.init_app(app)  

    @app.before_request
    def _set_csp_nonce():
        g.csp_nonce = secrets.token_urlsafe(16)

    app.jinja_env.globals["csp_nonce"] = lambda: g.csp_nonce

    _script_hashes, _style_hashes = csp_hashes.compute_hashes(Path(config.BASE_DIR) / "templates")
    _script_src_attr = " ".join(f"'sha256-{h}'" for h in _script_hashes)
    _style_src_attr = " ".join(f"'sha256-{h}'" for h in _style_hashes)
    logger.info(
        "CSP: %d event-handler hashes, %d style-attribute hashes computed from templates/",
        len(_script_hashes), len(_style_hashes),
    )

    @app.after_request
    def set_security_headers(response):
        nonce = g.get("csp_nonce", "")
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self'; "
            f"script-src-elem 'self' 'nonce-{nonce}'; "
            f"script-src-attr 'unsafe-hashes' {_script_src_attr}; "
            "style-src 'self'; "
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
        if request.path.startswith("/static/") and response.status_code < 400:
            response.headers["Cache-Control"] = f"public, max-age={config.STATIC_CACHE_MAX_AGE_SECONDS}"
        if os.environ.get("FLASK_ENV") != "development":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    for bp_module in (misc, auth, dashboard, documents, calendar, chat, admin, research, grades, progress, demo, webhooks):
        app.register_blueprint(bp_module.bp)

    with app.app_context():
        extensions.init_db()

    return app
