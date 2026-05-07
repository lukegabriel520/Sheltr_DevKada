"""Compatibility entrypoint for the Sheltr Flask app."""

import os

from safe_server import app

__all__ = ["app"]


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    if os.environ.get("USE_WAITRESS", "").strip().lower() in ("1", "true", "yes"):
        from waitress import serve

        raw_threads = (os.environ.get("WAITRESS_THREADS") or "").strip()
        threads = max(1, min(32, int(raw_threads))) if raw_threads else 4
        serve(app, host="0.0.0.0", port=port, threads=threads)
    else:
        app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
