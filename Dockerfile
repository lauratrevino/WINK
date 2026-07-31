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
