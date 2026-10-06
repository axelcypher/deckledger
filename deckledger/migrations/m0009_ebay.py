"""eBay: a connected seller account, its listings and sales, listing drafts made from cards, and
eBay listings watched as a price source (deckledger/ebay.py).

ebay_accounts: one connected eBay account per user and its OAuth tokens.
ebay_listings: the account's own listings as eBay last reported them; variant_id is the card a
listing sells, where it is known (made from a draft, or linked by hand).
ebay_sales: the sales eBay reported, one row per transaction.
ebay_drafts: listings prepared from cards but not (yet) put on eBay; once published they keep
item_id and status 'published'.
ebay_tracked_items: anyone's listings a user follows for a card's price. Like a manual price they
belong to the catalogue, not to an account.

variant_id is deliberately no foreign key anywhere: a listing or sale outlives the catalogue
entry, and catalog_sync keeps cards that are tracked (see its "referenced" list).
"""

NAME = "ebay"


def apply(connection, context):
    context.run_script("""
CREATE TABLE ebay_accounts (
  user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  username TEXT NOT NULL DEFAULT '', access_token TEXT NOT NULL DEFAULT '', access_expires_at TEXT NOT NULL DEFAULT '',
  refresh_token TEXT NOT NULL DEFAULT '', refresh_expires_at TEXT NOT NULL DEFAULT '', environment TEXT NOT NULL DEFAULT 'production',
  connected_at TEXT NOT NULL, listings_synced_at TEXT, last_error TEXT NOT NULL DEFAULT ''
);
CREATE TABLE ebay_listings (
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, item_id TEXT NOT NULL,
  variant_id TEXT, title TEXT NOT NULL DEFAULT '', sku TEXT NOT NULL DEFAULT '',
  price REAL, currency TEXT NOT NULL DEFAULT '', quantity INTEGER NOT NULL DEFAULT 0, quantity_sold INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'active', url TEXT NOT NULL DEFAULT '', image_url TEXT NOT NULL DEFAULT '',
  watch_count INTEGER NOT NULL DEFAULT 0, started_at TEXT NOT NULL DEFAULT '', ended_at TEXT NOT NULL DEFAULT '', synced_at TEXT NOT NULL,
  PRIMARY KEY(user_id, item_id)
);
CREATE TABLE ebay_sales (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  item_id TEXT NOT NULL, transaction_id TEXT NOT NULL, title TEXT NOT NULL DEFAULT '', sku TEXT NOT NULL DEFAULT '',
  quantity INTEGER NOT NULL DEFAULT 1, price REAL, currency TEXT NOT NULL DEFAULT '', buyer TEXT NOT NULL DEFAULT '', sold_at TEXT NOT NULL DEFAULT '',
  UNIQUE(user_id, item_id, transaction_id)
);
CREATE TABLE ebay_drafts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  game_id TEXT NOT NULL, variant_id TEXT NOT NULL, sheet_id INTEGER,
  title TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '', price REAL, currency TEXT NOT NULL DEFAULT 'EUR',
  quantity INTEGER NOT NULL DEFAULT 1, condition TEXT NOT NULL DEFAULT '400010', aspects TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'draft', item_id TEXT, error TEXT NOT NULL DEFAULT '', fees REAL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, published_at TEXT
);
CREATE INDEX idx_ebay_drafts_user ON ebay_drafts(user_id, game_id, status);
CREATE TABLE ebay_tracked_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT, variant_id TEXT NOT NULL, item_id TEXT NOT NULL,
  url TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '', price REAL, currency TEXT NOT NULL DEFAULT '', shipping REAL,
  status TEXT NOT NULL DEFAULT 'pending', image_url TEXT NOT NULL DEFAULT '', end_date TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
  added_by INTEGER, created_at TEXT NOT NULL, checked_at TEXT,
  UNIQUE(variant_id, item_id)
);
CREATE INDEX idx_ebay_tracked_checked ON ebay_tracked_items(checked_at);
""")
