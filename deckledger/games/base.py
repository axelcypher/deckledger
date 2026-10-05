"""The shape of a game: everything the rest of the application asks about one.

A game module (lorcana.py, one_piece.py, ...) fills in a `Game` and nothing else needs to know
which game it is talking to. A game an admin adds in the UI has no module; it gets `Game`'s
defaults, which describe a plain card game with four-copy playsets and no special rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

RARITY_FALLBACK_RANK = 900


def fetch_html(url: str) -> str:
    """A publisher's page as text, for the hooks that look up images and logos there."""
    request = Request(url, headers={"User-Agent": "DeckLedger/1.0", "Accept": "text/html"})
    return urlopen(request, timeout=18).read(2_000_000).decode("utf-8", "ignore")


def any_other_finish(variant, game) -> bool:
    return variant["finish"] != "Normal"


def generic_market_links(variant, attributes, mapping, search_term) -> dict:
    """For a game without a module: its mapped product page, else a marketplace search."""
    labels = {"cardmarket": "Cardmarket", "tcgplayer": "TCGplayer", "yuyutei": "Yuyutei"}
    return {
        "price_source": labels.get(variant.get("price_provider")) or "TCGplayer",
        "price_url": (mapping or {}).get("source_url") or f"https://www.tcgplayer.com/search/all/product?q={quote_plus(search_term)}&view=grid",
        "image_source": attributes.get("imageSource") or "Katalog-Provider",
        "image_source_url": attributes.get("imageSourceUrl") or "",
    }


@dataclass(frozen=True)
class Game:
    # ---- identity (seeded into the games table) -------------------------------------------------
    id: str = ""
    module_id: str = ""
    name: str = ""
    short_name: str = ""
    module_version: str = "1.0.0"
    languages: tuple[str, ...] = ()
    accent: str = "#6366f1"
    symbol: str = "◆"                      # shown where a game has no room for its logo
    icon: str = "generic"                  # static/tcg-icons/<icon>.svg
    logo_url: str | None = None            # official wordmark, fetched once and cached

    # ---- collecting ----------------------------------------------------------------------------
    # Copies of one card that make a full playset. Drives Playset% and the surplus a sheet offers.
    playset_size: int = 4
    # Ordinal rank per printed rarity label, least to most rare; unknown labels sort last.
    rarity_order: dict[str, int] = field(default_factory=dict)
    # Language-independent keys for an icon-based rarity filter -> rank.
    rarity_filter_keys: dict[str, int] = field(default_factory=dict)
    # Ranks that are their own printing next to the base card (listed separately, outside Base%).
    premium_ranks: frozenset[int] = frozenset()
    # The cost filter's last button means "this or more".
    cost_filter_cap: int | None = None
    # The finishes a card tile offers quantity buttons for, per print run: (id, label, finish of
    # the regular print, finish of its foil counterpart, what the foil button is called). Several
    # entries give the catalogue a switch between them; none means one button for the base print.
    tile_editions: tuple[tuple[str, str, str, str, str], ...] = ()
    # Whether a variant counts for Foil%.
    is_foil: Callable[[dict, "Game"], bool] = any_other_finish
    # set_type values (lower case) of the sets that make up "the main game" on the dashboard.
    main_set_types: tuple[str, ...] = ()

    # ---- decks ---------------------------------------------------------------------------------
    formats: tuple[dict, ...] = ()
    ruleset: str | None = None             # default for games.deck_ruleset
    zone_for_card_type: dict[str, str] = field(default_factory=dict)
    validate_deck: Callable[[dict, list, dict], tuple[list, list]] | None = None
    # Copies of one card a deck may hold, by card type; everything else: playset_size.
    copy_limits: dict[str, int] = field(default_factory=dict)
    # Card types that can be played at all (empty: every type).
    playable_types: tuple[str, ...] = ()
    unplayable_message: str = "{rarity}-Karten sind nicht spielbar."
    # set_type (lower case) -> why its cards cannot go into a deck.
    excluded_deck_set_types: dict[str, str] = field(default_factory=dict)
    # Language the deck builder's catalogue opens with.
    deck_language: str = "all"
    # The builder lists one printing per card across all sets (reprints under the same number).
    deck_catalog_across_printings: bool = False
    # A zone the builder fills up with a standard card: (zone, size, variant id).
    auto_fill_zone: tuple[str, int, str] | None = None

    # ---- sources -------------------------------------------------------------------------------
    provider: tuple[int, int, int] | None = None   # (minimum sets, minimum cards, timeout seconds)
    price_method: str | None = None
    price_overrides: tuple[tuple[str, str], ...] = ()   # (language, method)
    cardmarket_game_id: int | None = None
    # Fractions of width/height to cut off each side where the publisher shows its print files.
    image_bleed: tuple[float, float] | None = None
    # (size in bytes, SHA-256) of stand-in images the publisher serves for a scan it has not
    # published yet; such a download counts as missing, so the next image source gets its turn.
    image_placeholders: tuple[tuple[int, str], ...] = ()
    # The publisher offers separate foil layers for its cards (ravensburger_foil.py).
    official_foil_layers: bool = False
    # row -> URL of the card image, for variants the provider gave no imageUrl.
    remote_image_url: Callable[[dict], str | None] | None = None
    # set row -> URL of the set's official visual.
    remote_set_visual: Callable[[dict], str | None] | None = None
    # set row, public folder -> a set logo shipped with the app, if there is one.
    bundled_set_logo: Callable[[dict, object], object] | None = None
    # (variant, variant attributes, marketplace mapping, search term) -> price/image source links.
    market_links: Callable[[dict, dict, dict | None, str], dict] = generic_market_links

    def rarity_rank(self, rarity) -> int:
        return self.rarity_order.get(rarity, RARITY_FALLBACK_RANK)

    def copy_limit(self, card_type) -> int:
        return self.copy_limits.get(card_type, self.playset_size)

    def format(self, format_id) -> dict:
        """The format with this id, else the game's first one, else an empty profile."""
        return next((item for item in self.formats if item["id"] == format_id), self.formats[0] if self.formats else {})

    def deck_card_error(self, card, adding: bool) -> str | None:
        """Why a card may not go into a deck of this game, or None."""
        reason = self.excluded_deck_set_types.get(str(card["set_type"] or "").lower())
        if reason:
            return reason
        if adding and self.playable_types and card["card_type"] not in self.playable_types:
            return self.unplayable_message.format(rarity=card["rarity"])
        return None

    def client_rules(self) -> dict:
        """What the frontend needs to know, sent along with the game in the bootstrap data."""
        return {
            "playset_size": self.playset_size, "copy_limits": self.copy_limits, "symbol": self.symbol, "icon": self.icon,
            "tile_editions": [dict(zip(("id", "label", "regular", "foil", "foil_label"), edition)) for edition in self.tile_editions],
        }
