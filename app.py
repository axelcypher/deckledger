"""Entry point: `gunicorn app:app` in the container, `python app.py` for a local debug server.

The application itself is the `ophir_cards` package next to this file.
"""
from ophir_cards import app
from ophir_cards.web import require_secret_key

# gunicorn and docker-entrypoint.sh import this module; without a SECRET_KEY of their own they get
# no further. Only the local debug server may sign sessions with the public development key.
require_secret_key(debug=__name__ == "__main__")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)
