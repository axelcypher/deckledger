#!/bin/sh
set -eu

# Migrations run before the app serves anything.
python -c "import app"

# Catalogue and prices are brought up to date in the background, so a new container serves the
# stored catalogue at once instead of being unreachable while they load (a deploy used to mean a
# minute without the app). Prices change daily; the catalogue only grows when a set is released or
# revealed, so it is re-fetched once its last successful import is older than CATALOG_REFRESH_HOURS.
(python catalog_sync.py --if-needed || echo "Katalogimport fehlgeschlagen; zuletzt gespeicherter Katalog bleibt aktiv." >&2
 python price_sync.py --if-needed || echo "Preisimport nicht erreichbar; letzte gültige Preise bleiben aktiv." >&2
 while sleep 21600; do
  python catalog_sync.py --if-needed --max-age-hours "${CATALOG_REFRESH_HOURS:-24}" || echo "Geplanter Katalogimport fehlgeschlagen; letzter Stand bleibt aktiv." >&2
  python price_sync.py --if-needed || echo "Geplanter Preisimport fehlgeschlagen; letzter Stand bleibt aktiv." >&2
 done) &

# Comments on the posts users linked to their sheets. Reddit allows an anonymous client about
# one request a minute, so each run reads at most one post.
(while sleep 60; do
  python post_watch.py || echo "Post-Watcher fehlgeschlagen." >&2
done) &

# eBay: followed listings (card prices) and connected accounts' own listings, when due.
(while sleep 600; do
  python ebay_sync.py || echo "eBay-Abgleich fehlgeschlagen." >&2
done) &

exec gunicorn --bind 0.0.0.0:8080 --workers 2 --threads 4 --access-logfile - app:app
