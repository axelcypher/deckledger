"""What differs from game to game: formats, deck rules, rarity ladders, playset sizes, defaults."""

import re

from .config import jload


GAME_LOGOS = {
    "lorcana": "https://www.disneylorcana.com/_nuxt/logo-br-2x.Sweb4xgr.png",
    "one-piece": "https://en.onepiece-cardgame.com/renewal/images/common/logo_op_white.png",
    "hololive": "https://en.hololive-official-cardgame.com/wp-content/themes/tcg_en/assets/img/global/logo_w.svg",
    "vcard": "https://cdn.gamersupps.gg/images/Nav-Logo.png",
}

FORMAT_PROFILES = {
    "lorcana": [
        {"id": "core", "name": "Core Constructed", "description": "Rotierender offizieller Kartenpool", "zones": [{"id":"main","name":"Deck","target":60}], "rules_url": "https://www.disneylorcana.com/en-US/play/ways-to-play"},
        {"id": "infinity", "name": "Infinity Constructed", "description": "Nicht-rotierender offizieller Kartenpool", "zones": [{"id":"main","name":"Deck","target":60}], "rules_url": "https://www.disneylorcana.com/en-US/play/ways-to-play"},
        {"id": "coconut", "name": "Coconut (Open Beta)", "description": "Offizieller thematischer Multiplayer-Betatest", "zones": [{"id":"main","name":"Deck","target":60}], "rules_url": "https://files.disneylorcana.com/FormatCoconut_Rules.pdf"},
    ],
    "one-piece": [
        {"id": "standard", "name": "Official Standard", "description": "Aktuelle offizielle Regulation", "zones": [{"id":"leader","name":"Leader","target":1},{"id":"main","name":"Deck","target":50},{"id":"don","name":"DON!!","target":10}], "rules_url": "https://en.onepiece-cardgame.com/rules/"},
    ],
    "hololive": [
        {"id": "standard", "name": "Official Standard", "description": "Offizielles Constructed-Regelset", "zones": [{"id":"oshi","name":"Oshi","target":1},{"id":"main","name":"Main Deck","target":50},{"id":"cheer","name":"Cheer Deck","target":20}], "rules_url": "https://en.hololive-official-cardgame.com/wp-content/themes/tcg_en/assets/img/rule/official_rule_book_ver1_02.pdf"},
    ],
    "vcard": [
        {"id": "standard", "name": "Official Standard", "description": "Offizielles Constructed-Regelset", "zones": [{"id":"main","name":"Deck","target":50}], "rules_url": "https://cdn.gamersupps.gg/VCARD/files/VCard+Game+Rules.pdf"},
    ],
}


def validate_lorcana_deck(deck, cards, counts):
    errors, warnings = [], []
    is_coconut = deck["format_id"] == "coconut"
    if counts.get("main", 0) < 60:
        errors.append(f'Noch {60-counts.get("main",0)} Karten bis zum Minimum von 60.')
    names = {}
    colors = set()
    for c in cards:
        names[c["canonical_name"]] = names.get(c["canonical_name"], 0) + c["quantity"]
        attrs = jload(c["attributes"], {})
        colors.add(attrs.get("color"))
        if str(c.get("set_type") or "").lower() == "quest":
            errors.append(f'{c["canonical_name"]} ist eine Quest-Karte und nicht für Constructed-Decks zulässig.')
        if deck["format_id"] == "core" and attrs.get("legality") in ("future", "rotated", "banned"):
            errors.append(f'{c["canonical_name"]} ist im Core-Format nicht legal.')
    color_limit = 3 if is_coconut else 2
    if len(colors - {None}) > color_limit:
        errors.append(f"Das Deck enthält mehr als {color_limit} Tintenfarben.")
    if is_coconut:
        repeated = [name for name, qty in names.items() if qty > 1]
        if len(repeated) > 1:
            errors.append("Im Coconut-Format darf nur die zur Coconut-Karte gehörende Charakterkarte mehrfach enthalten sein.")
        for name in repeated:
            qty = names[name]
            matching_cards = [card for card in cards if card["canonical_name"] == name]
            if qty > 4:
                errors.append(f'{name}: auch die Coconut-Charakterkarte ist auf 4 Exemplare begrenzt.')
            if any(card.get("card_type") != "Character" for card in matching_cards):
                errors.append(f'{name}: nur die zugehörige Charakterkarte darf mehrfach enthalten sein.')
        warnings.append("Coconut-Karte und passende Tintenfarbe bitte mit der offiziellen Companion App abgleichen.")
    else:
        for name, qty in names.items():
            if qty > 4:
                errors.append(f'{name}: maximal 4 Exemplare erlaubt.')
    return errors, warnings


def validate_one_piece_deck(deck, cards, counts):
    errors, warnings = [], []
    if counts.get("leader", 0) != 1:
        errors.append("Genau 1 Leader ist erforderlich.")
    if counts.get("main", 0) != 50:
        errors.append(f'Hauptdeck: {counts.get("main",0)}/50 Karten.')
    # Explicit DON!! artwork is optional. Any open slots are represented by
    # the standard DON!! card in the builder and do not have to be stored.
    if counts.get("don", 0) > 10:
        errors.append(f'DON!!-Deck: maximal 10 Karten ({counts.get("don",0)}/10).')
    numbers = {}
    for c in cards:
        if c["zone"] == "main":
            numbers[c["collector_number"]] = numbers.get(c["collector_number"], 0) + c["quantity"]
    for number, qty in numbers.items():
        if qty > 4:
            errors.append(f'{number}: maximal 4 Exemplare erlaubt.')
    if any(c["card_type"] != "Leader" for c in cards if c["zone"] == "leader"):
        errors.append("In der Leader-Zone sind nur Leader-Karten erlaubt.")
    if any(c["card_type"] == "Leader" or c["card_type"] == "DON!!" for c in cards if c["zone"] == "main"):
        errors.append("Leader- und DON!!-Karten dürfen nicht ins Hauptdeck.")
    if any(c["card_type"] != "DON!!" for c in cards if c["zone"] == "don"):
        errors.append("Das DON!!-Deck darf nur DON!!-Karten enthalten.")
    leader = next((c for c in cards if c["zone"] == "leader"), None)
    if leader:
        leader_color = jload(leader["attributes"], {}).get("color")
        invalid = [c["canonical_name"] for c in cards if c["zone"] == "main" and jload(c["attributes"], {}).get("color") != leader_color]
        if invalid:
            warnings.append(f'{len(invalid)} Karten entsprechen nicht der Leader-Farbe {leader_color}.')
    return errors, warnings


def validate_hololive_deck(deck, cards, counts):
    errors, warnings = [], []
    if counts.get("oshi", 0) != 1:
        errors.append("Genau 1 Oshi holomem ist erforderlich.")
    if counts.get("main", 0) != 50:
        errors.append(f'Main Deck: {counts.get("main",0)}/50 Karten.')
    if counts.get("cheer", 0) != 20:
        errors.append(f'Cheer Deck: {counts.get("cheer",0)}/20 Karten.')
    numbers = {}
    for c in cards:
        if c["zone"] == "main":
            numbers[c["collector_number"]] = numbers.get(c["collector_number"], 0) + c["quantity"]
    for number, qty in numbers.items():
        if qty > 4:
            errors.append(f'{number}: maximal 4 Exemplare im Main Deck erlaubt.')
    if any(zone_for_card_type("hololive-standard", c["card_type"]) != "oshi" for c in cards if c["zone"] == "oshi"):
        errors.append("In der Oshi-Zone ist nur ein Oshi holomem erlaubt.")
    if any(zone_for_card_type("hololive-standard", c["card_type"]) != "main" for c in cards if c["zone"] == "main"):
        errors.append("Oshi- und Cheer-Karten dürfen nicht ins Main Deck.")
    if any(zone_for_card_type("hololive-standard", c["card_type"]) != "cheer" for c in cards if c["zone"] == "cheer"):
        errors.append("Das Cheer Deck darf nur Cheer-Karten enthalten.")
    return errors, warnings


VCARD_PLAYABLE_TYPES = ("VT", "Mascot", "Support", "World")


def validate_vcard_deck(deck, cards, counts):
    errors, warnings = [], []
    if counts.get("main", 0) != 50:
        errors.append(f'Deck: {counts.get("main",0)}/50 Karten.')
    copies, elements, has_pl8 = {}, set(), False
    for c in cards:
        attrs = jload(c["attributes"], {})
        if c["card_type"] not in VCARD_PLAYABLE_TYPES:
            errors.append(f'{c["canonical_name"]} ({c["rarity"]}) ist eine Sammelkarte und nicht spielbar.')
            continue
        # A Secret Rare or Paradox is the same card as its regular printing, so copies are counted
        # per name (which carries the Power Level for VTs) and type, not per printing.
        key = (c["canonical_name"].lower(), c["card_type"], attrs.get("cost"))
        copies.setdefault(key, [c["canonical_name"], 0])[1] += c["quantity"]
        has_pl8 = has_pl8 or (c["card_type"] == "VT" and attrs.get("cost") == 8)
        # Supports carry no element and Neutral Worlds fit every deck.
        if attrs.get("color") and attrs["color"] != "Neutral":
            elements.add(attrs["color"])
    for (_, card_type, _), (name, qty) in copies.items():
        limit = 2 if card_type == "Mascot" else 3
        if qty > limit:
            errors.append(f'{name}: maximal {limit} Exemplare erlaubt.')
    if len(elements) > 2:
        errors.append(f'Das Deck enthält {len(elements)} Elemente ({", ".join(sorted(elements))}); maximal 2 sind erlaubt.')
    if cards and not has_pl8:
        errors.append("Das Deck braucht mindestens 1 PL8 VT.")
    return errors, warnings


# Bespoke deck-legality rules per game stay code (they're real business logic,
# not data), but which ruleset a game uses is a `games.deck_ruleset` value,
# not a hardcoded game_id check -- see deck_validation() / zone_for_card_type().
DECK_RULESETS = {
    "lorcana-standard": {"zone_for_card_type": {}, "validate": validate_lorcana_deck},
    "one-piece-standard": {"zone_for_card_type": {"Leader": "leader", "DON!!": "don"}, "validate": validate_one_piece_deck},
    "hololive-standard": {
        "zone_for_card_type": {"Oshi": "oshi", "Oshi holomem": "oshi", "推しホロメン": "oshi", "Cheer": "cheer", "エール": "cheer"},
        "validate": validate_hololive_deck,
    },
    "vcard-standard": {"zone_for_card_type": {}, "validate": validate_vcard_deck},
}


def zone_for_card_type(deck_ruleset, card_type):
    mapping = (DECK_RULESETS.get(deck_ruleset) or {}).get("zone_for_card_type", {})
    return mapping.get(str(card_type or "").strip(), "main")


GAME_DATA = [
    ("lorcana", "disney-lorcana", "Disney Lorcana", "Lorcana", "1.0.0", ["DE", "EN"], "#8b5cf6"),
    ("one-piece", "one-piece-card-game", "One Piece Card Game", "One Piece", "1.0.0", ["EN", "JP"], "#ef4444"),
    ("hololive", "hololive-ocg", "hololive Official Card Game", "hololive", "0.9.0", ["EN", "JP"], "#06b6d4"),
    ("vcard", "vcard-tcg", "VCard Trading Card Game", "VCard", "0.9.0", ["EN"], "#f5c451"),
]

# Ordinal rarity rank per game, least to most rare. Printings carry the rarity label in the
# printing's own language, so Lorcana needs both the English and the official German terms
# mapped to the same rank (verified against Ravensburger's 8-tier ladder incl. the Epic/Iconic
# tiers added with Fabled in Sept. 2025: Common<Uncommon<Rare<Super Rare<Legendary<Epic<Enchanted
# <Iconic). Codes with no confidently-known slot (promos, DON!!, unclear hololive tiers) are left
# unmapped and sort after every ranked rarity rather than guessing a position.
RARITY_ORDER = {
    "lorcana": {
        "Common": 0, "Gewöhnlich": 0,
        "Uncommon": 1, "Ungewöhnlich": 1,
        "Rare": 2, "Selten": 2,
        "Super Rare": 3, "Episch": 3,
        "Legendary": 4, "Legendär": 4,
        "Epic": 5, "Mythisch": 5,
        "Enchanted": 6, "Verzaubert": 6,
        "Iconic": 7, "Ikonisch": 7,
        "Special": 8, "Speziell": 8,
    },
    "one-piece": {
        "C": 0, "UC": 1, "R": 2, "SR": 3, "SEC": 4, "L": 5,
        "SP": 6, "SP CARD": 6, "SPカード": 6, "SP P": 6, "TR": 7,
    },
    "hololive": {
        "C": 0, "U": 1, "R": 2, "RR": 3, "SR": 4, "OSR": 5, "SEC": 6, "HR": 6,
    },
    # VCard's official checklist groups by a label that is part card type, part rarity. Mascot,
    # Support and World are the common-slot cards, so they sort ahead of the VT rarity ladder.
    "vcard": {
        "Mascot": 0, "Support": 1, "World": 2, "Uncommon": 3, "Rare": 4, "Ultra Rare": 5,
        "Secret Rare": 6, "Paradox": 7, "Box Topper": 8, "Promo": 9, "God Rare": 10,
    },
}
RARITY_FALLBACK_RANK = 900

# Copies of one card that make a full playset -- the constructed copy limit. Drives Playset%
# and what the trade sheet picker offers as surplus.
PLAYSET_SIZES = {"vcard": 3}
DEFAULT_PLAYSET_SIZE = 4


def playset_size(game_id):
    return PLAYSET_SIZES.get(game_id, DEFAULT_PLAYSET_SIZE)

# Language-independent keys for Lorcana's icon-based rarity filter (one key covers both the
# English and German printed label, since a single card can appear under either depending on
# which language is being viewed).
LORCANA_RARITY_KEYS = {
    "common": 0, "uncommon": 1, "rare": 2, "super-rare": 3, "legendary": 4,
    "epic": 5, "enchanted": 6, "iconic": 7, "special": 8,
}
LORCANA_PREMIUM_RANKS = {LORCANA_RARITY_KEYS["epic"], LORCANA_RARITY_KEYS["enchanted"], LORCANA_RARITY_KEYS["iconic"]}


def rarity_rank(game_id, rarity):
    return RARITY_ORDER.get(game_id, {}).get(rarity, RARITY_FALLBACK_RANK)


def matches_catalog_filters(game_id, rarity, attributes, selected_rarities, selected_costs, selected_colors, inkwell):
    """Shared in-memory counterpart to the set/deck catalogue filters.

    Collection and watchlist rows are already small user-specific result sets, so applying the
    same normalized gameplay filters after loading them keeps their UI and semantics identical
    to the set catalogue without duplicating game-specific SQL in both endpoints.
    """
    attributes = attributes or {}
    if selected_rarities:
        selected_ranks = {LORCANA_RARITY_KEYS[key] for key in selected_rarities if key in LORCANA_RARITY_KEYS}
        if selected_ranks and rarity_rank(game_id, rarity) not in selected_ranks:
            return False
    if selected_costs:
        try:
            card_cost = int(float(attributes.get("cost")))
        except (TypeError, ValueError):
            return False
        if not any(card_cost >= 7 if value == "7" and game_id == "lorcana" else card_cost == int(value) for value in selected_costs if value.isdigit()):
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
    order = RARITY_ORDER.get(game_id)
    if not order:
        return f"{column} COLLATE NOCASE"
    whens = " ".join(f"WHEN '{rarity.replace(chr(39), chr(39) * 2)}' THEN {rank}" for rarity, rank in order.items())
    return f"CASE {column} {whens} ELSE {RARITY_FALLBACK_RANK} END"
# (minimum_sets, minimum_cards, timeout_seconds) per shipped Tier-2 provider.
# Code itself lives in providers/<id>.py -- read at seed time, not embedded
# here, so it stays normal syntax-highlighted, lintable Python in the repo.
DEFAULT_PROVIDERS = {
    "lorcana": (15, 2500, 300),
    "one-piece": (20, 1000, 600),
    "hololive": (10, 500, 600),
    "vcard": (4, 1200, 300),
}


DEFAULT_DECK_RULESETS = {"lorcana": "lorcana-standard", "one-piece": "one-piece-standard", "hololive": "hololive-standard", "vcard": "vcard-standard"}
# VCard has no entry on purpose: neither Cardmarket nor TCGplayer lists it, so its prices stay
# empty rather than estimated (an admin can still assign a method once a marketplace carries it).
DEFAULT_PRICE_METHODS = {"lorcana": "cardmarket", "one-piece": "cardmarket", "hololive": "tcgcsv"}
DEFAULT_PRICE_OVERRIDES = [("hololive", "JP", "yuyutei")]
# Cardmarket's numeric idGame per bootstrapped game -- unauthenticated and stable,
# but there is no discovery endpoint for it, so a new TCG's id is a one-time
# manual lookup on cardmarket.com, entered once via the admin UI (games.cardmarket_game_id).
DEFAULT_CARDMARKET_GAME_IDS = {"lorcana": 19, "one-piece": 18}
