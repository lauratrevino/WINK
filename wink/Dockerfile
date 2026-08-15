FROM python:3.11-slim

# tesseract-ocr is the system binary pytesseract shells out to for image OCR
# (see wink/services/documents.py). Without it, image uploads still work —
# extract_text() falls back to a placeholder — but no text gets pulled out
# of them.
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p /app/uploads
EXPOSE 10000

# NOTE (deliberately not done here): running as a non-root USER is the
# standard hardening move and was requested, but this container writes
# to /app/uploads at runtime, and if that path is backed by a mounted
# persistent disk on Render (recommended separately — see the uploads-
# persistence note elsewhere), external volume mounts commonly come back
# root-owned regardless of what this Dockerfile chown's at build time.
# Switching to a non-root user here without being able to verify actual
# mount ownership on Render risks a silent permission failure on every
# upload — the wrong thing to introduce right before a live test. Once
# the persistent disk is confirmed and there's a chance to verify in
# staging, add a startup step that chowns /app/uploads before dropping
# from root to an unprivileged user (e.g. via a small entrypoint script
# using gosu/su-exec), rather than a bare USER line.

# Worker count is configurable via WEB_CONCURRENCY (Render and several other
# PaaS providers set this automatically based on instance size; defaults to
# 2 here for local/small deployments). A common starting formula for CPU-bound
# work is (2 x CPU cores) + 1 — since most of this app's per-request time is
# spent waiting on the DB or the Anthropic API rather than burning CPU, threads
# do a lot of the concurrency work within each worker (see --threads below),
# so you generally don't need as many workers as that formula suggests.
# --max-requests + jitter recycles each worker periodically, which bounds the
# damage from any slow memory growth over a long-running process — cheap
# insurance once this is serving hundreds of students continuously instead of
# restarting often during development.
CMD gunicorn --bind 0.0.0.0:10000 \
    --workers ${WEB_CONCURRENCY:-2} \
    --worker-class gthread --threads 8 \
    --timeout 120 \
    --max-requests 1000 --max-requests-jitter 100 \
    app:app
