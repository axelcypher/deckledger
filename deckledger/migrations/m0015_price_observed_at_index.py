"""An index on price_observations(observed_at).

Every page load asks for the newest observation of any card (/api/bootstrap's price_sync,
deckledger/pages.py), and price_sync.py's monthly fold reads and deletes whole months by date.
Without this both scanned the entire price history, which only ever grows.
"""

NAME = "price observed_at index"


def apply(connection, context):
    connection.execute("CREATE INDEX idx_prices_observed_at ON price_observations(observed_at)")
