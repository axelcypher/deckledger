"""Deals: what was sold, bought or traded, with whom, and what became of it.

deals: one agreement with one partner. status is open, reserved, done or cancelled; received_at
is when the cards coming in were confirmed to have arrived (they are in the collection only
from then on).
deal_cards: the cards on its two sides ('give', 'get') with the price each stands for. name and
detail are kept as they were, and variant_id is deliberately no foreign key: the history has to
outlive whatever happens to the catalogue. booked is how many copies the deal took out of or put
into the collection -- what a cancellation gives back.
deal_events: what happened to a deal and when.
"""

NAME = "deals"


def apply(connection, context):
    connection.execute("""CREATE TABLE deals (
      id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id),
      game_id TEXT NOT NULL REFERENCES games(id),
      partner TEXT NOT NULL DEFAULT '', platform TEXT NOT NULL DEFAULT '', url TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
      status TEXT NOT NULL DEFAULT 'open',
      money_in REAL NOT NULL DEFAULT 0, money_out REAL NOT NULL DEFAULT 0, shipping REAL NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
      reserved_at TEXT, completed_at TEXT, received_at TEXT, cancelled_at TEXT
    )""")
    connection.execute("CREATE INDEX idx_deals_user_game ON deals(user_id, game_id, status)")
    connection.execute("""CREATE TABLE deal_cards (
      id INTEGER PRIMARY KEY AUTOINCREMENT, deal_id INTEGER NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
      side TEXT NOT NULL, variant_id TEXT NOT NULL, quantity INTEGER NOT NULL DEFAULT 1, unit_price REAL,
      name TEXT NOT NULL DEFAULT '', detail TEXT NOT NULL DEFAULT '', booked INTEGER NOT NULL DEFAULT 0,
      UNIQUE(deal_id, side, variant_id)
    )""")
    connection.execute("CREATE INDEX idx_deal_cards_variant ON deal_cards(variant_id)")
    connection.execute("""CREATE TABLE deal_events (
      id INTEGER PRIMARY KEY AUTOINCREMENT, deal_id INTEGER NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
      at TEXT NOT NULL, kind TEXT NOT NULL, detail TEXT NOT NULL DEFAULT ''
    )""")
