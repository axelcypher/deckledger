#!/bin/sh
set -eu

python -c "import app"
python catalog_sync.py --if-needed || echo "Katalogimport fehlgeschlagen; zuletzt gespeicherter Katalog bleibt aktiv." >&2
python price_sync.py --if-needed || echo "Preisimport nicht erreichbar; letzte gültige Preise bleiben aktiv." >&2

# Prices change daily; the catalogue only grows when a set is released or revealed, so it is
# re-fetched once its last successful import is older than CATALOG_REFRESH_HOURS.
(while sleep 21600; do
  python catalog_sync.py --if-needed --max-age-hours "${CATALOG_REFRESH_HOURS:-24}" || echo "Geplanter Katalogimport fehlgeschlagen; letzter Stand bleibt aktiv." >&2
  python price_sync.py --if-needed || echo "Geplanter Preisimport fehlgeschlagen; letzter Stand bleibt aktiv." >&2
done) &

exec gunicorn --bind 0.0.0.0:8080 --workers 2 --threads 4 --access-logfile - app:app
