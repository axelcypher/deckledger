"""The overviews that add up a whole game at once: the set overview, the dashboard's reel and
recent additions, the deck builder's catalogue pages."""
from conftest import BOOST, ELSA, EMBER8, EMBER8_HOLO, EMBER9, SPARK_SET_TWO, TIDE8, query


def observe(variant_id, amount, observed_at="2026-01-01T06:00:00+00:00"):
    game_id = variant_id.split("-")[0]
    query("INSERT OR IGNORE INTO marketplace_products VALUES('cardmarket',?,?,?,'https://example.com/p','test',?,'{}')",
          (f"cm-{variant_id}", variant_id, game_id, observed_at))
    query("INSERT INTO price_observations(variant_id,provider_id,metric,amount,currency,observed_at) VALUES(?,'cardmarket','trend',?,'EUR',?)",
          (variant_id, amount, observed_at))


def add(client, variant_id, quantity, condition="Near Mint"):
    assert client.post("/api/collection", json={"variant_id": variant_id, "delta": quantity, "condition": condition}).status_code == 200


def test_set_overview_counts_every_set_of_the_game(client):
    observe(EMBER8, 2.0)
    observe(TIDE8, 10.0)
    observe(SPARK_SET_TWO, 1.25)
    add(client, EMBER8, 3)
    add(client, EMBER8, 1, "Played")    # a second row of the same card counts its copies, not a second card
    add(client, EMBER8_HOLO, 1)
    add(client, TIDE8, 3)               # exactly a playset (VCard: 3)
    add(client, SPARK_SET_TWO, 2)
    add(client, ELSA, 4)                # another game's card counts in none of these sets
    # A row left at zero copies (imports and backups can write one) is not a card owned.
    query("INSERT INTO collection_entries(user_id,variant_id,condition,quantity) VALUES(1,?,'Near Mint',0)", (EMBER9,))
    # A printing without any variant is no card of the set.
    query("INSERT INTO card_identities(id,game_id,canonical_name,card_type,attributes) VALUES('vcard-card-ghost','vcard','Ghost','VT','{}')")
    query("INSERT INTO printings(id,identity_id,game_id,set_id,collector_number,language,rarity,attributes) VALUES('vcard-print-ghost-en','vcard-card-ghost','vcard','vcard-test','999','EN','Rare','{}')")
    # A set announced before any of its cards exist is left out.
    query("INSERT INTO sets(id,game_id,code,name,set_type,release_date,printed_card_count,classifications,accent) VALUES('vcard-empty','vcard','9','Empty','Booster Set','2026-05-01',0,'[]','#000')")
    # Product release dates of the printings win over the set's own date, every one of them.
    query("UPDATE printings SET attributes=json_object('releaseProductReleaseDate','2024-07-01') WHERE id=?", ("vcard-print-spark2-en",))
    query("UPDATE printings SET attributes=json_object('releaseProductReleaseDate','2025-03-01') WHERE id=?", ("vcard-print-ember8-en",))
    query("UPDATE printings SET attributes=json_object('releaseProductReleaseDate','2025-02-01') WHERE id=?", ("vcard-print-tide8-en",))

    sets = {item["id"]: item for item in client.get("/api/games/vcard/sets").get_json()}
    assert list(sets) == ["vcard-test", "vcard-two"]
    first, second = sets["vcard-test"], sets["vcard-two"]
    totals = query("""SELECT COUNT(DISTINCT p.identity_id) cards, COUNT(*) variants FROM printings p JOIN variants v ON v.printing_id=p.id
                      WHERE p.set_id='vcard-test'""")[0]
    assert (first["owned"], first["total"], first["value"]) == (2, totals["cards"], 38.0)
    assert first["base_completion"] == round(2 / totals["cards"] * 100)
    # Owned variants: Ember Normal, Ember Holo, Tide Normal -- not the Ember PL9 row at zero.
    assert first["master_completion"] == first["foil_completion"] == round(3 / totals["variants"] * 100) != round(4 / totals["variants"] * 100)
    # Ember (4 Normal + 1 Holo) and Tide (exactly 3) are playsets.
    assert first["playset_completion"] == round(2 / totals["cards"] * 100) != round(1 / totals["cards"] * 100)
    assert first["release_dates"] == ["2025-02-01", "2025-03-01"]
    assert (second["owned"], second["total"], second["value"], second["base_completion"], second["release_dates"]) == (1, 1, 2.5, 100, ["2024-07-01"])
    lorcana = client.get("/api/games/lorcana/sets").get_json()
    assert [(item["owned"], item["base_completion"]) for item in lorcana] == [(1, 50)]


def test_set_overview_of_an_untouched_collection(client):
    sets = client.get("/api/games/lorcana/sets").get_json()
    assert [(item["id"], item["owned"], item["value"], item["base_completion"], item["playset_completion"]) for item in sets] == [("lorcana-1", 0, 0, 0, 0)]


def test_dashboard_reel_shows_one_print_per_card_newest_set_first(client):
    observe(EMBER8, 2.0)
    observe(SPARK_SET_TWO, 1.25)
    for variant_id in (EMBER8, EMBER8_HOLO, EMBER9, SPARK_SET_TWO, ELSA):
        add(client, variant_id, 1)

    slides = client.get("/api/home-banner").get_json()["slides"]
    assert {slide["game_id"] for slide in slides} == {"vcard", "lorcana"}
    vcard = client.get("/api/home-banner?game_id=vcard").get_json()["slides"]
    assert [slide["game_id"] for slide in vcard] == ["vcard"] and vcard == [slide for slide in slides if slide["game_id"] == "vcard"]
    cards = vcard[0]["cards"]
    assert len({card["identity_id"] for card in cards}) == len(cards) == 3
    assert [card["set_code"] for card in cards] == ["1", "1", "2"]
    # The Normal print stands for Ember, and every card comes with its current price.
    prices = {card["variant_id"]: card["price"] for card in cards}
    assert prices == {EMBER8: 2.0, EMBER9: None, SPARK_SET_TWO: 1.25}
    assert client.get("/api/home-banner?game_id=one-piece").get_json() == {"slides": []}


def test_dashboard_reel_by_value_skips_unpriced_cards(client):
    observe(EMBER8, 2.0)
    observe(TIDE8, 10.0)
    for variant_id in (EMBER8, TIDE8, EMBER9):
        add(client, variant_id, 1)
    client.post("/api/settings", json={"homeBanner": {"modes": ["value"]}})
    cards = client.get("/api/home-banner?game_id=vcard").get_json()["slides"][0]["cards"]
    assert [(card["variant_id"], card["price"]) for card in cards] == [(TIDE8, 10.0), (EMBER8, 2.0)]


def test_recent_additions_newest_first_one_per_card(client):
    observe(TIDE8, 10.0)
    for variant_id in (EMBER8, EMBER8_HOLO, TIDE8, BOOST, SPARK_SET_TWO, EMBER9):
        add(client, variant_id, 1)
    for position, variant_id in enumerate((EMBER8, EMBER8_HOLO, TIDE8, BOOST, SPARK_SET_TWO, EMBER9)):
        query("UPDATE collection_entries SET last_added_at=? WHERE variant_id=?", (f"2026-02-0{position + 1}T00:00:00+00:00", variant_id))
    recent = client.get("/api/home-recent?game_id=vcard").get_json()
    assert [card["variant_id"] for card in recent] == [EMBER9, SPARK_SET_TWO, BOOST, TIDE8, EMBER8_HOLO]
    assert [card["price"] for card in recent] == [None, None, None, 10.0, None]
    assert client.get("/api/home-recent").get_json() == []


def test_deck_catalogue_pages_add_up_to_the_total(client):
    # Enough cards for several pages of the smallest page size (24).
    for index in range(40):
        query("INSERT INTO card_identities(id,game_id,canonical_name,card_type,attributes) VALUES(?,'vcard',?,'Support','{}')", (f"extra-{index}", f"Extra {index:02d}"))
        query("INSERT INTO printings(id,identity_id,game_id,set_id,collector_number,language,rarity,attributes) VALUES(?,?,'vcard','vcard-test',?,'EN','Support','{}')",
              (f"extra-{index}-en", f"extra-{index}", f"{200 + index}"))
        query("INSERT INTO variants(id,printing_id,game_id,variant_code,finish,attributes) VALUES(?,?,'vcard','normal','Normal','{}')", (f"extra-{index}-normal", f"extra-{index}-en"))

    def page(**parameters):
        return client.get("/api/deckbuilder/catalog", query_string={"game_id": "vcard", "limit": 24, **parameters}).get_json()

    everything = page(limit=200)
    total = everything["pagination"]["total"]
    assert total == len(everything["cards"]) > 48
    seen = []
    for offset in range(0, total, 24):
        part = page(offset=offset)
        assert part["pagination"]["total"] == total
        assert part["pagination"]["has_more"] == (offset + 24 < total)
        seen += [card["variant_id"] for card in part["cards"]]
    assert seen == [card["variant_id"] for card in everything["cards"]]
    assert "matches" not in everything["cards"][0]
    # Past the last page the total is still told; a search counts its own matches.
    assert page(offset=total + 50)["pagination"] == {"offset": total + 50, "limit": 24, "total": total, "has_more": False}
    extra = page(q="Extra")
    assert extra["pagination"]["total"] == 40 and len(extra["cards"]) == 24
    assert page(q="no such card")["pagination"]["total"] == 0


def test_owned_only_search_is_the_collections_share_of_the_full_search(client):
    add(client, EMBER8, 2)
    add(client, EMBER9, 1)
    add(client, EMBER9, -1)                 # had it once: not owned any more
    query("INSERT INTO collection_entries(user_id,variant_id,condition,quantity) VALUES(1,?,'Played',0)", (EMBER9,))  # a row left at zero
    add(client, TIDE8, 1, "Played")
    full = client.get("/api/search", query_string={"q": "PL8|PL9", "limit": 80}).get_json()
    owned = client.get("/api/search", query_string={"q": "PL8|PL9", "limit": 80, "owned": 1}).get_json()
    assert [row["variant_id"] for row in owned] == [row["variant_id"] for row in full if row["variant_id"] in (EMBER8, TIDE8)]
    assert {row["variant_id"]: row["quantity"] for row in owned} == {EMBER8: 2, TIDE8: 1}


def test_card_detail_adds_up_every_copy_of_its_own_prints_only(client):
    add(client, EMBER8, 2)
    add(client, EMBER8, 1, "Played")
    assert client.post("/api/collection", json={"variant_id": EMBER8, "delta": 1, "is_graded": True, "grade_label": "PSA 9", "price_override": 40}).status_code == 200
    add(client, EMBER8_HOLO, 1)
    add(client, TIDE8, 5)                   # another card in the collection stays out of it
    variants = {variant["id"]: variant for variant in client.get("/api/cards/vcard-card-ember8").get_json()["variants"]}
    assert (variants[EMBER8]["quantity"], variants[EMBER8]["override_value"], variants[EMBER8]["unpriced_quantity"]) == (4, 40.0, 3)
    assert variants[EMBER8_HOLO]["quantity"] == 1
    assert all(variant["quantity"] == 0 for key, variant in variants.items() if key not in (EMBER8, EMBER8_HOLO))


def test_every_print_of_a_card_carries_its_attributes(client):
    """The attributes are read once per card and shared by its prints -- the foil print, which
    stands for the card in the foil view, included."""
    data = client.get("/api/sets/lorcana-1/cards", query_string={"finish": "foil", "costs": "4"}).get_json()
    assert [card["canonical_name"] for card in data["cards"]] == ["Elsa - Snow Queen"]
    elsa = data["cards"][0]
    assert elsa["finish"] == "Silver" and elsa["identity_attrs"] == {"color": "Amethyst", "cost": 4}
    assert all(variant["identity_attrs"] == {"color": "Amethyst", "cost": 4} for variant in elsa["variants"])


def test_the_dashboard_counts_each_game_in_its_own_language(client):
    observe(EMBER8, 2.0)
    observe(ELSA, 7.5)
    add(client, EMBER8, 3)
    add(client, EMBER8, 1, "Played")
    add(client, TIDE8, 1)
    add(client, ELSA, 2)

    def figures():
        return {game["id"]: (game["copies"], game["unique_cards"], game["value"]) for game in client.get("/api/bootstrap").get_json()["games"]}

    # Each game counts in its first language unless one is chosen: Lorcana's is German, Elsa is English.
    shown = figures()
    assert shown == {game_id: {"vcard": (5, 2, 8.0)}.get(game_id, (0, 0, 0)) for game_id in shown}
    client.post("/api/settings", json={"defaultLanguages": {"vcard": "DE", "lorcana": "EN"}})
    shown = figures()
    assert shown == {game_id: {"lorcana": (2, 1, 15.0)}.get(game_id, (0, 0, 0)) for game_id in shown}
