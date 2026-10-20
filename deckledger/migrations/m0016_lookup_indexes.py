"""Indexes for lookups that scanned a whole table every time they ran.

- ebay_listings(user_id, variant_id): a sheet looks up the cheapest active listing of each of
  its cards (deckledger/sheets.py EBAY_LISTING_SQL); the primary key only narrowed that down to
  every listing the user ever had, and listings are kept after they end.
- card_identities(canonical_name COLLATE NOCASE): a card named in a text import or a pasted
  decklist (deckledger/backup.py, deckledger/decks.py) -- once per line.
- deal_events(deal_id), inbox_items(post_id): their parents delete them by cascade and a deal
  reads its history; neither had an index on the reference.
- applied_requests(created_at): every quantity change clears out the entries older than 30 days.
"""

NAME = "lookup indexes"


def apply(connection, context):
    context.run_script("""
CREATE INDEX idx_ebay_listings_variant ON ebay_listings(user_id, variant_id);
CREATE INDEX idx_card_identities_name ON card_identities(canonical_name COLLATE NOCASE);
CREATE INDEX idx_deal_events_deal ON deal_events(deal_id);
CREATE INDEX idx_inbox_items_post ON inbox_items(post_id);
CREATE INDEX idx_applied_requests_created ON applied_requests(created_at);
""")
