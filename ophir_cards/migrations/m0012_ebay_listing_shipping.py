"""ebay_listings.shipping_cost: what a listing charges for shipping (the first shipping service),
so its price can be compared with the card's market value including shipping. NULL until the
listing was synced again.
"""

NAME = "ebay listing shipping"


def apply(connection, context):
    connection.execute("ALTER TABLE ebay_listings ADD COLUMN shipping_cost REAL")
