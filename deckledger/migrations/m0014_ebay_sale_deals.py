"""ebay_sales.deal_id: the deal (deckledger/deals.py) a sale was booked as. NULL = not yet, which
is every sale stored before this and a sale whose listing has no card yet; the next sync books
them. Deliberately no foreign key: a deal the user deletes must not come back with the next sync.
"""

NAME = "ebay sale deals"


def apply(connection, context):
    connection.execute("ALTER TABLE ebay_sales ADD COLUMN deal_id INTEGER")
