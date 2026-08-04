#!/bin/sh
set -eu

python -c "import app"
python catalog_sync.py --if-needed || echo "Katalogimport fehlgeschlagen; zuletzt gespeicherter Katalog bleibt aktiv." >&2
python price_sync.py --if-needed || echo "Preisimport nicht erreichbar; letzte gültige Preise bleiben aktiv." >&2

(while sleep 21600; do
  python price_sync.py --if-needed || echo "Geplanter Preisimport fehlgeschlagen; letzter Stand bleibt aktiv." >&2
done) &

exec gunicorn --bind 0.0.0.0:8080 --workers 2 --threads 4 --access-logfile - app:app
