"""DeckLedger: a self-hosted collection manager for trading card games.

Importing the package builds the whole application: every module registers its routes on the one
Flask app, then the database is created or migrated. `app.py` in the repository root is the entry
point servers and scripts import.
"""
from . import config, games, web, schema  # noqa: F401  -- the order is the dependency order
from . import prices, images, assets, catalog, auth, pages, collection, watchlists, sheets, decks, backup, account, admin  # noqa: F401
from .web import app

schema.init_database()

__all__ = ["app"]
