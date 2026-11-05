"""Entry point: `gunicorn app:app` in the container, `python app.py` for a local debug server.

The application itself is the `ophir_cards` package next to this file.
"""
from ophir_cards import app

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)
