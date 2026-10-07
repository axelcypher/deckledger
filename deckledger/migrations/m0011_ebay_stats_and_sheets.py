"""eBay listing statistics, and sheets that follow the listings of their cards.

ebay_accounts.scopes: the OAuth scopes the user granted when connecting; a token is only ever
renewed for those (asking for more fails), so a scope added later (sell.analytics.readonly, for
views) takes a new connection.
ebay_listings.view_count / impression_count / click_through_rate / conversion_rate: eBay's traffic
report for the listing over the last 90 days; NULL until it was read.
trade_sheets.ebay_sync: whether the sheet takes quantity and price of its cards from their active
eBay listings (on unless switched off).
trade_sheet_cards.ebay_item: the listing an entry was last updated from ('' = never), so a sold-out
listing can take its card off the sheet and an ended one can give the price label back.
"""

NAME = "ebay stats and sheets"


def apply(connection, context):
    context.run_script("""
ALTER TABLE ebay_accounts ADD COLUMN scopes TEXT NOT NULL DEFAULT '';
ALTER TABLE ebay_listings ADD COLUMN view_count INTEGER;
ALTER TABLE ebay_listings ADD COLUMN impression_count INTEGER;
ALTER TABLE ebay_listings ADD COLUMN click_through_rate REAL;
ALTER TABLE ebay_listings ADD COLUMN conversion_rate REAL;
ALTER TABLE trade_sheets ADD COLUMN ebay_sync INTEGER NOT NULL DEFAULT 1;
ALTER TABLE trade_sheet_cards ADD COLUMN ebay_item TEXT NOT NULL DEFAULT '';
""")
