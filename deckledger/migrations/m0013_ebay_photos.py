"""ebay_photos: a user's own photos of a card for its eBay listings (deckledger/ebay_photos.py).

The files live next to the database in ebay-photos/: <id>-original.jpg as uploaded (upright) and
<id>.jpg cut from it by crop (JSON: x, y, w, h as fractions, rotate in degrees). eps_url is the
picture on eBay's servers and eps_uploaded_at when it was put there ('' = not yet, or the crop
changed since). variant_id is no foreign key, as everywhere in the eBay tables.
"""

NAME = "ebay photos"


def apply(connection, context):
    context.run_script("""
CREATE TABLE ebay_photos (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  variant_id TEXT NOT NULL, position INTEGER NOT NULL DEFAULT 0, crop TEXT NOT NULL DEFAULT '',
  eps_url TEXT NOT NULL DEFAULT '', eps_uploaded_at TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX idx_ebay_photos_card ON ebay_photos(user_id, variant_id, position);
""")
