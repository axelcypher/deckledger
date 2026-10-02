"""What differs from game to game, in one place per game.

lorcana.py, one_piece.py, hololive.py and vcard.py each describe their game as a `Game` (base.py):
formats and deck rules, rarity ladder, playset size, where images, logos and prices come from.
The rest of the application asks `game(game_id)` and never branches on a game's id itself. Adding
a game means adding one module here and listing it in GAMES.
"""

import re

from . import hololive, lorcana, one_piece, vcard
from .base import RARITY_FALLBACK_RANK, Game

GAMES = {module.GAME.id: module.GAME for module in (lorcana, one_piece, hololive, vcard)}
# A game created in the admin UI has rows in the database but no module: plain defaults.
GENERIC = Game()
# Which deck rules a game uses is a value in the games table (games.deck_ruleset), so an admin
# can point a game of their own at an existing ruleset.
DECK_RULESETS = {item.ruleset: item for item in GAMES.values() if item.ruleset}

__all__ = ["DECK_RULESETS", "GAMES", "GENERIC", "Game", "RARITY_FALLBACK_RANK", "game", "matches_catalog_filters",
           "playset_size", "rarity_case_sql", "rarity_filter_ranks", "rarity_rank", "zone_for_card_type"]


def game(game_id) -> Game:
    return GAMES.get(game_id, GENERIC)


def playset_size(game_id) -> int:
    return game(game_id).playset_size


def rarity_rank(game_id, rarity) -> int:
    return game(game_id).rarity_rank(rarity)


def rarity_filter_ranks(game_id, keys) -> set:
    """The ranks the keys of a game's icon-based rarity filter stand for."""
    table = game(game_id).rarity_filter_keys
    return {table[key] for key in keys or () if key in table}


def cost_matches(game_id, card_cost, selected_costs) -> bool:
    """Whether a card's cost is among the selected filter values; a game's highest button can
    mean "this or more"."""
    cap = game(game_id).cost_filter_cap
    return any(card_cost >= cap if cap is not None and int(value) == cap else card_cost == int(value) for value in selected_costs if str(value).isdigit())


def zone_for_card_type(deck_ruleset, card_type) -> str:
    rules = DECK_RULESETS.get(deck_ruleset)
    return (rules.zone_for_card_type if rules else {}).get(str(card_type or "").strip(), "main")


def matches_catalog_filters(game_id, rarity, attributes, selected_rarities, selected_costs, selected_colors, inkwell):
    """Shared in-memory counterpart to the set/deck catalogue filters.

    Collection and watchlist rows are already small user-specific result sets, so applying the
    same normalized gameplay filters after loading them keeps their UI and semantics identical
    to the set catalogue without duplicating game-specific SQL in both endpoints.
    """
    attributes = attributes or {}
    if selected_rarities:
        selected_ranks = rarity_filter_ranks(game_id, selected_rarities)
        if selected_ranks and rarity_rank(game_id, rarity) not in selected_ranks:
            return False
    if selected_costs:
        try:
            card_cost = int(float(attributes.get("cost")))
        except (TypeError, ValueError):
            return False
        if not cost_matches(game_id, card_cost, selected_costs):
            return False
    if selected_colors:
        card_colors = {part for part in re.split(r"[-/]", str(attributes.get("color") or "")) if part}
        if not card_colors.intersection(selected_colors):
            return False
    if inkwell in {"true", "false"}:
        raw = attributes.get("inkwell")
        actual = raw is True or raw == 1 or str(raw).lower() in {"true", "1", "yes"}
        if actual != (inkwell == "true"):
            return False
    return True


def rarity_case_sql(game_id, column):
    """SQL CASE mirroring rarity_rank(), for ORDER BY on paginated queries. Rarity labels are our
    own constants (never user input), so inlining them as string literals is safe."""
    order = game(game_id).rarity_order
    if not order:
        return f"{column} COLLATE NOCASE"
    whens = " ".join(f"WHEN '{rarity.replace(chr(39), chr(39) * 2)}' THEN {rank}" for rarity, rank in order.items())
    return f"CASE {column} {whens} ELSE {RARITY_FALLBACK_RANK} END"
