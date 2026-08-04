"""Tier-2 custom-code provider: Lorcana, sourced from LorcanaJSON.

Self-contained on purpose (Tier-2 code runs in an isolated subprocess with no
access to the rest of the app): only stdlib + catalog_provider_contract.
"""

import json

from catalog_provider_contract import empty_catalog, fetch, put_identity, slug


# LorcanaJSON keeps the original gameplay set in ``setCode`` for promotional
# reprints. Their physical catalogue set is supplied separately in
# ``promoGrouping``. These are source-owned group codes, not synthetic sets.
LORCANA_PROMO_GROUPS = {
    "P1": ("Promo Set 1", "Promo Set"),
    "P2": ("Promo Set 2", "Promo Set"),
    "P3": ("Promo Set 3", "Promo Set"),
    "P4": ("Promo Set 4", "Promo Set"),
    "C1": ("Lorcana Challenge 1", "Challenge Promo"),
    "C2": ("Lorcana Challenge 2", "Challenge Promo"),
    "D23": ("D23 Collection", "Special Collection"),
    "PD1": ("Product Series 1", "Product Promo"),
    "CC1": ("Curator's Collection: Heroines Edition", "Special Collection"),
    "DIS": ("Discover Promo", "Promo Set"),
}

# Known physical misprints in the German TFC print run: Ravensburger corrected these in later
# print runs, but the original error cards are real, physically distinct, collectible printings
# that Cardmarket lists as their own product. Keyed by LorcanaJSON's card id, which for TFC's
# base numbering matches the printed collector number (verified per entry against the DB).
LORCANA_DE_TFC_MISPRINTS = {
    "21": {  # Stitch - Carefree Surfer, #21/204
        "artwork_suffix": "misprint-1-lore",
        "edition_label": "Fehldruck · 1 Legendenpunkt",
        "image_url": "https://patagiumgames.com/cdn/shop/files/IMG_2869.jpg?v=1700057930",
        "image_source": "Patagium Games – Foto der physischen Fehldruckkarte",
        "image_source_url": "https://patagiumgames.com/products/lorcana-2023-stitch-carefree-surfer-sorgloser-surfer-german-language-misprint",
        "price_url": "https://www.cardmarket.com/de/Lorcana/Products/Singles/The-First-Chapter/Stitch-Carefree-Surfer-V3?language=3",
        "errata": "Der erste deutsche Druck zeigt 1 statt 2 Legendenpunkte.",
        "extra_attributes": {"printedLore": 1, "correctedLore": 2},
    },
    "2": {  # Ariel/Arielle - Spectacular Singer / Spektakuläre Sängerin, #2/204
        "artwork_suffix": "misprint-artist-credit",
        "edition_label": "Fehldruck · falscher Artist-Credit",
        "image_url": "https://product-images.s3.cardmarket.com/1629/1TFC/832568/832568.jpg",
        "image_source": "Cardmarket – Foto der physischen Fehldruckkarte",
        "image_source_url": "https://www.cardmarket.com/de/Lorcana/Products/Singles/The-First-Chapter/Ariel-Spectacular-Singer-V2",
        "price_url": "https://www.cardmarket.com/de/Lorcana/Products/Singles/The-First-Chapter/Ariel-Spectacular-Singer-V2",
        "errata": "Der erste deutsche Druck nennt fälschlich Michael \"Cookie\" Niewiadomy statt Alice Pisoni als Artist.",
        "extra_attributes": {"printedArtist": "Michael \"Cookie\" Niewiadomy", "correctedArtist": "Alice Pisoni"},
    },
}


def fetch_catalog() -> dict:
    catalog = empty_catalog()
    root = "https://lorcanajson.org/files/current"
    payloads = {}
    for language in ("en", "de"):
        url = f"{root}/{language}/allCards.json"
        payloads[language.upper()] = json.loads(fetch(url))
        catalog["sources"][f"lorcana_{language}"] = url

    english_names = {str(c["id"]): c for c in payloads["EN"]["cards"]}
    accents = ["#7c3aed", "#2563eb", "#db2777", "#0891b2", "#d97706", "#059669"]
    for language, payload in payloads.items():
        for code, source_set in payload["sets"].items():
            set_id = f"lorcana-{slug(code)}"
            existing = catalog["sets"].get(set_id)
            if existing is None or language == "EN":
                counts = source_set.get("cardCounts", {})
                catalog["sets"][set_id] = {
                    "id": set_id,
                    "game_id": "lorcana",
                    "code": str(code),
                    "name": source_set.get("name") or str(code),
                    "set_type": source_set.get("type", "set").replace("_", " ").title(),
                    "release_date": source_set.get("releaseDate"),
                    "printed_card_count": counts.get("total") or counts.get("base"),
                    "classifications": list(source_set.get("allowedInFormats", {}).keys()),
                    "accent": accents[(int(source_set.get("number") or 1) - 1) % len(accents)],
                    "_source_language": language,
                }

        for card in payload["cards"]:
            card_key = str(card["id"])
            # baseId links a promotional/reprint card back to the same
            # language-independent gameplay identity. The concrete source card
            # id remains part of the printing/variant id and therefore still
            # distinguishes the physical release.
            identity_key = str(card.get("baseId") or card_key)
            canonical = english_names.get(identity_key, english_names.get(card_key, card))
            identity_id = f"lorcana-card-{slug(identity_key)}"
            identity_attrs = {
                "color": canonical.get("color"),
                "cost": canonical.get("cost"),
                "inkwell": canonical.get("inkwell"),
                "strength": canonical.get("strength"),
                "willpower": canonical.get("willpower"),
                "lore": canonical.get("lore"),
                "subtypes": canonical.get("subtypes", []),
                "allowedInFormats": canonical.get("allowedInFormats", {}),
                "source": "LorcanaJSON / Ravensburger",
                "sourceLanguage": language,
            }
            put_identity(catalog, {
                "id": identity_id,
                "game_id": "lorcana",
                "canonical_name": canonical.get("fullName") or canonical.get("name") or card_key,
                "rules_text": canonical.get("fullText") or "",
                "card_type": canonical.get("type") or "Unknown",
                "attributes": identity_attrs,
            }, prefer=language == "EN")

            promo_group = card.get("promoGrouping")
            physical_set_code = str(promo_group or card["setCode"])
            set_id = f"lorcana-{slug(physical_set_code)}"
            if promo_group:
                promo_name, promo_type = LORCANA_PROMO_GROUPS.get(
                    physical_set_code,
                    (f"Promo Group {physical_set_code}", "Promo Set"),
                )
                existing = catalog["sets"].get(set_id)
                if existing is None or language == "EN":
                    catalog["sets"][set_id] = {
                        "id": set_id,
                        "game_id": "lorcana",
                        "code": physical_set_code,
                        "name": promo_name,
                        "set_type": promo_type,
                        "release_date": None,
                        "printed_card_count": 0,
                        "classifications": ["Promo"],
                        "accent": accents[(len(catalog["sets"]) - 1) % len(accents)],
                        "_source_language": language,
                    }
            printing_id = f"lorcana-print-{slug(card_key)}-{language.lower()}"
            links = card.get("externalLinks", {})
            printing_attrs = {
                "localizedName": card.get("fullName") or card.get("name"),
                "localizedRulesText": card.get("fullText"),
                "fullIdentifier": card.get("fullIdentifier"),
                "artists": card.get("artists", []),
                "baseId": card.get("baseId"),
                "promoGrouping": promo_group,
                "promoSource": card.get("promoSource"),
                "promoSourceCategory": card.get("promoSourceCategory"),
                "externalLinks": links,
                "sourceUrl": catalog["sources"][f"lorcana_{language.lower()}"],
            }
            catalog["printings"][printing_id] = {
                "id": printing_id,
                "identity_id": identity_id,
                "game_id": "lorcana",
                "set_id": set_id,
                "collector_number": str(card.get("number", card_key)),
                "language": language,
                "rarity": card.get("rarity") or "Unknown",
                "attributes": printing_attrs,
            }
            foil_types = card.get("foilTypes") or ["None"]
            for foil in foil_types:
                code = "normal" if foil == "None" else slug(foil)
                image_url = (card.get("images") or {}).get("full")
                variant_id = f"{printing_id}-{code}"
                catalog["variants"][variant_id] = {
                    "id": variant_id,
                    "printing_id": printing_id,
                    "game_id": "lorcana",
                    "variant_code": code,
                    "finish": "Normal" if foil == "None" else foil,
                    "artwork_id": card_key,
                    "is_parallel": 0 if foil == "None" else 1,
                    "source_type": "lorcanajson",
                    "attributes": {
                        "imageUrl": image_url,
                        "imageSourceUrl": "https://lorcanajson.org/",
                        "catalogSourceUrl": catalog["sources"][f"lorcana_{language.lower()}"],
                        "priceUrl": links.get("cardmarketUrl") or links.get("tcgPlayerUrl") or links.get("cardTraderUrl"),
                        "priceSource": "Cardmarket" if links.get("cardmarketUrl") else "TCGplayer" if links.get("tcgPlayerUrl") else None,
                    },
                }
            # A handful of TFC cards physically exist in a pre-errata German printing that
            # Ravensburger later corrected; Cardmarket lists each as its own product, and
            # they're real, distinct, collectible printings in their own right.
            misprint = LORCANA_DE_TFC_MISPRINTS.get(card_key)
            if language == "DE" and physical_set_code == "1" and not promo_group and misprint:
                for code, finish, parallel in (
                    (f"normal-{misprint['artwork_suffix']}", "Normal", 0),
                    (f"silver-{misprint['artwork_suffix']}", "Silver", 1),
                ):
                    variant_id = f"{printing_id}-{code}"
                    catalog["variants"][variant_id] = {
                        "id": variant_id,
                        "printing_id": printing_id,
                        "game_id": "lorcana",
                        "variant_code": code,
                        "finish": finish,
                        "artwork_id": f"{card_key}-{misprint['artwork_suffix']}",
                        "is_parallel": parallel,
                        "source_type": "physical-errata-printing",
                        "attributes": {
                            "imageUrl": misprint["image_url"],
                            "imageSource": misprint["image_source"],
                            "imageSourceUrl": misprint["image_source_url"],
                            "catalogSourceUrl": catalog["sources"]["lorcana_de"],
                            "priceUrl": misprint["price_url"],
                            "priceSource": "Cardmarket",
                            "editionLabel": misprint["edition_label"],
                            "isMisprint": True,
                            "errata": misprint["errata"],
                            **misprint.get("extra_attributes", {}),
                        },
                    }
    # Count physical EN printings per set. This prevents the two imported
    # languages from inflating the number shown in the set browser.
    for record in catalog["sets"].values():
        record["printed_card_count"] = sum(
            1 for printing in catalog["printings"].values()
            if printing["set_id"] == record["id"] and printing["language"] == "EN"
        )
    return catalog
