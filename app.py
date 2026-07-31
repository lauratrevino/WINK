"""
Entry point. Kept as a top-level app.py (rather than moving the gunicorn
target) so existing deploy config — `gunicorn app:app` — doesn't need to
change. All actual application code lives in wink/ (see wink/__init__.py
for the app factory and a map of what moved where).
"""
from wink import create_app

app = create_app()

if __name__ == "__main__":
    import os
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)), debug=False)
