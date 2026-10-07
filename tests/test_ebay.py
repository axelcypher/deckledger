"""eBay: listings followed as a card's price, drafts from the collection and from sheets, publishing
them, and the connected account's own listings. eBay itself is replaced by a fake (FakeEbay)."""
import json
import re
from urllib.parse import parse_qs

import pytest

from conftest import ADMIN, EMBER8, EMBER9, TIDE8, deckledger, login, query
from test_prices_and_assets import observe, price_of

ITEM = "123456789012"
OTHER_ITEM = "210987654321"


class Response:
    def __init__(self, status_code=200, payload=None, content=None):
        self.status_code = status_code
        self.payload = payload
        self.content = content if content is not None else json.dumps(payload or {}).encode()

    def json(self):
        if self.payload is None:
            raise ValueError("no json")
        return self.payload


def trading(call, body):
    return Response(content=f'<?xml version="1.0"?><{call}Response xmlns="urn:ebay:apis:eBLBaseComponents"><Ack>Success</Ack>{body}</{call}Response>'.encode())


class FakeEbay:
    """Answers like eBay; `items` are the public listings, `selling` the account's lists."""

    def __init__(self):
        self.items = {}
        self.calls = []
        self.selling = {"ActiveList": "", "UnsoldList": "", "SoldList": ""}
        self.fail = {}
        self.public_key = ""
        self.groups = set()
        self.traffic = {}
        self.traffic_params = None

    def __call__(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if url.endswith("/identity/v1/oauth2/token"):
            form = kwargs["data"]
            if form["grant_type"] == "authorization_code" and form["code"] != "good":
                return Response(400, {"error": "invalid_grant"})
            return Response(payload={"access_token": f'token-{form["grant_type"]}', "expires_in": 7200,
                                     "refresh_token": "refresh", "refresh_token_expires_in": 47304000})
        if "/sell/analytics/v1/traffic_report" in url:
            self.traffic_params = kwargs["params"]
            return Response(payload={"header": {"metrics": [{"key": key} for key in kwargs["params"]["metric"].split(",")]},
                                     "records": [{"dimensionValues": [{"value": item}], "metricValues": [{"value": value} for value in values]}
                                                 for item, values in self.traffic.items()]})
        if "/commerce/notification/v1/public_key/" in url:
            return Response(payload={"algorithm": "ECDSA", "digest": "SHA1", "key": self.public_key})
        if "/commerce/identity/v1/user/" in url:
            return Response(payload={"username": "kartenhai"})
        if "/buy/browse/v1/item/get_item_by_legacy_id" in url:
            params = kwargs["params"]
            key = params["legacy_item_id"] + (f'-{params["legacy_variation_id"]}' if "legacy_variation_id" in params else "")
            if key in self.groups:
                return Response(400, {"errors": [{"errorId": 11006, "message": f"The legacy Id is invalid. Use https://api.ebay.com/buy/browse/v1/item/get_items_by_item_group?item_group_id={key} to get the item group details."}]})
            item = self.items.get(key)
            return Response(404, {"errors": [{"errorId": 11001}]}) if item is None else Response(payload=item)
        if "/sell/account/v1/" in url:
            kind = url.rsplit("/", 1)[-1].split("_")[0]
            key, id_key = {"fulfillment": ("fulfillmentPolicies", "fulfillmentPolicyId"), "payment": ("paymentPolicies", "paymentPolicyId"),
                           "return": ("returnPolicies", "returnPolicyId")}[kind]
            return Response(payload={key: [{id_key: f"{kind}-1", "name": f"{kind} policy"}]})
        if url.endswith("/ws/api.dll"):
            call = kwargs["headers"]["X-EBAY-API-CALL-NAME"]
            if call in self.fail:
                return trading(call, f"<Errors><SeverityCode>Error</SeverityCode><LongMessage>{self.fail[call]}</LongMessage></Errors>").__class__(
                    content=f'<{call}Response xmlns="urn:ebay:apis:eBLBaseComponents"><Ack>Failure</Ack><Errors><SeverityCode>Error</SeverityCode><LongMessage>{self.fail[call]}</LongMessage></Errors></{call}Response>'.encode())
            if call == "UploadSiteHostedPictures":
                return trading(call, "<SiteHostedPictureDetails><FullURL>https://i.ebayimg.com/card.jpg</FullURL></SiteHostedPictureDetails>")
            if call in ("AddFixedPriceItem", "VerifyAddFixedPriceItem"):
                return trading(call, "<ItemID>555000111222</ItemID><Fees><Fee><Name>ListingFee</Name><Fee currencyID=\"EUR\">0.35</Fee></Fee></Fees>")
            if call == "GetMyeBaySelling":
                body = kwargs["data"].decode()
                name = next(name for name in self.selling if f"<{name}><Include>true" in body)
                return trading(call, self.selling[name])
        raise AssertionError(f"unexpected request {method} {url}")


@pytest.fixture
def ebay(monkeypatch):
    fake = FakeEbay()
    monkeypatch.setattr(deckledger.ebay, "http", fake)
    return fake


@pytest.fixture
def configured(ebay):
    assert login(ADMIN).post("/api/admin/ebay", json={"client_id": "app", "client_secret": "secret", "ru_name": "Deck-Ledger-ru"}).status_code == 200
    return ebay


def listing(price, currency="EUR", **extra):
    return {"title": "Ember PL8 VCard", "price": {"value": str(price), "currency": currency}, "itemWebUrl": f"https://www.ebay.de/itm/x{price}",
            "shippingOptions": [{"shippingCost": {"value": "1.60", "currency": "EUR"}}], "image": {"imageUrl": "https://i.ebayimg.com/x.jpg"},
            "estimatedAvailabilities": [{"estimatedAvailabilityStatus": "IN_STOCK"}], **extra}


def connect(user_id=1):
    query("""INSERT INTO ebay_accounts(user_id,username,access_token,access_expires_at,refresh_token,refresh_expires_at,connected_at)
             VALUES(?,'kartenhai','token','2999-01-01T00:00:00+00:00','refresh','2999-01-01T00:00:00+00:00','t')""", (user_id,))


# ---- Followed listings as a price ---------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    (f"https://www.ebay.de/itm/Ember-PL8-VCard/{ITEM}?hash=item1c", ITEM),
    (f"https://www.ebay.com/itm/{ITEM}", ITEM),
    (f"https://cgi.ebay.de/ws/eBayISAPI.dll?ViewItem&item={ITEM}", ITEM),
    (f"  {ITEM} ", ITEM),
    ("https://www.ebay.de/sch/i.html?_nkw=ember", None),
    (f"https://www.ebay.de/itm/{ITEM}?itmmeta=01M4B&hash=item34fb%3Ag&var=526719127153", f"{ITEM}-526719127153"),
    ("12345", None),
])
def test_item_ids_are_read_from_links(text, expected):
    assert deckledger.ebay.parse_item_id(text) == expected


def test_following_needs_the_application_keys(client, ebay):
    response = client.post(f"/api/variants/{EMBER8}/ebay-items", json={"url": ITEM})
    assert response.status_code == 409 and "nicht eingerichtet" in response.get_json()["error"]


def test_followed_listings_become_the_cards_price(client, configured):
    configured.items = {ITEM: listing(5), OTHER_ITEM: listing(7)}
    response = client.post(f"/api/variants/{EMBER8}/ebay-items", json={"url": f"https://www.ebay.de/itm/Ember/{ITEM}"})
    assert response.status_code == 201
    item = response.get_json()["items"][0]
    assert (item["status"], item["price"], item["shipping"], item["title"]) == ("active", 5.0, 1.6, "Ember PL8 VCard")
    client.post(f"/api/variants/{EMBER8}/ebay-items", json={"url": OTHER_ITEM})
    variant = price_of(client, EMBER8, "vcard-card-ember8")
    assert (variant["price"], variant["price_low"], variant["price_provider"], variant["price_source"]) == (6.0, 5.0, "ebay", "eBay-Angebote")
    assert variant["price_url"] == "https://www.ebay.de/itm/x5"
    client.post("/api/collection", json={"variant_id": EMBER8, "delta": 2})
    assert client.get("/api/collection?game_id=vcard").get_json()["stats"]["value"] == 12.0
    assert client.post(f"/api/variants/{EMBER8}/ebay-items", json={"url": ITEM}).status_code == 409


def test_cardmarket_and_manual_prices_stay_ahead(client, configured):
    configured.items = {ITEM: listing(5)}
    observe(EMBER8, "cardmarket", 9.0, "2026-09-01T06:00:00+00:00")
    client.post(f"/api/variants/{EMBER8}/ebay-items", json={"url": ITEM})
    assert price_of(client, EMBER8, "vcard-card-ember8")["price"] == 9.0


def test_an_ended_listing_keeps_the_last_price_until_it_is_removed(client, configured):
    configured.items = {ITEM: listing(5)}
    client.post(f"/api/variants/{EMBER8}/ebay-items", json={"url": ITEM})
    configured.items = {}
    items = client.post(f"/api/variants/{EMBER8}/ebay-items/refresh").get_json()["items"]
    assert (items[0]["status"], items[0]["price"]) == ("ended", 5.0)
    assert price_of(client, EMBER8, "vcard-card-ember8")["price"] == 5.0
    assert client.delete(f"/api/variants/{EMBER8}/ebay-items/{ITEM}").get_json()["items"] == []
    assert price_of(client, EMBER8, "vcard-card-ember8")["price"] is None
    assert query("SELECT COUNT(*) n FROM price_observations WHERE provider_id='ebay'")[0]["n"] == 0


def test_only_changes_are_stored(client, configured):
    configured.items = {ITEM: listing(5)}
    client.post(f"/api/variants/{EMBER8}/ebay-items", json={"url": ITEM})
    query("UPDATE price_observations SET observed_at='2026-09-01T10:00:00+00:00' WHERE provider_id='ebay'")
    client.post(f"/api/variants/{EMBER8}/ebay-items/refresh")
    assert query("SELECT COUNT(*) n FROM price_observations WHERE provider_id='ebay'")[0]["n"] == 2   # trend + low, unchanged
    configured.items = {ITEM: listing(4)}
    client.post(f"/api/variants/{EMBER8}/ebay-items/refresh")
    assert [row["amount"] for row in query("SELECT amount FROM price_observations WHERE provider_id='ebay' AND metric='trend' ORDER BY id")] == [5.0, 4.0]


def test_the_background_job_rereads_what_is_due(client, configured):
    configured.items = {ITEM: listing(5)}
    client.post(f"/api/variants/{EMBER8}/ebay-items", json={"url": ITEM})
    connection = deckledger.web.sqlite3.connect(deckledger.config.DB_PATH)
    connection.row_factory = deckledger.web.sqlite3.Row
    try:
        assert deckledger.ebay.run_due(connection)["tracked"] == 0
        connection.execute("UPDATE ebay_tracked_items SET checked_at='2020-01-01T00:00:00+00:00'")
        configured.items = {ITEM: listing(8)}
        assert deckledger.ebay.run_due(connection)["tracked"] == 1
    finally:
        connection.close()
    assert price_of(client, EMBER8, "vcard-card-ember8")["price"] == 8.0


def test_a_tracked_card_is_kept_by_the_catalogue_sync(client, configured):
    configured.items = {ITEM: listing(5)}
    client.post(f"/api/variants/{TIDE8}/ebay-items", json={"url": ITEM})
    from conftest import catalog_sync, sample_catalog
    catalog = sample_catalog()
    for key in [key for key, value in catalog["variants"].items() if key == TIDE8]:
        del catalog["variants"][key]
    catalog_sync.write_database(catalog, {"vcard"})
    assert query("SELECT COUNT(*) n FROM variants WHERE id=?", (TIDE8,))[0]["n"] == 1


# ---- Connecting ---------------------------------------------------------------------------------

def test_connecting_an_account(client, configured):
    assert client.get("/ebay/connect").headers["Location"].startswith("https://auth.ebay.com/oauth2/authorize?")
    with client.session_transaction() as session:
        state = session["ebay_oauth_state"]
    assert client.get("/ebay/callback?code=good&state=wrong").headers["Location"].endswith("/?ebay=state")
    client.get("/ebay/connect")
    with client.session_transaction() as session:
        state = session["ebay_oauth_state"]
    assert client.get(f"/ebay/callback?code=good&state={state}").headers["Location"].endswith("/?ebay=connected")
    status = client.get("/api/ebay/status").get_json()
    assert (status["connected"], status["username"], status["marketplace"]) == (True, "kartenhai", "EBAY_DE")
    token_call = next(call for call in configured.calls if call[1].endswith("/oauth2/token"))
    assert token_call[2]["data"]["redirect_uri"] == "Deck-Ledger-ru"
    assert client.post("/api/ebay/disconnect").get_json() == {"disconnected": True}
    assert client.get("/api/ebay/status").get_json()["connected"] is False


def test_an_expired_access_token_is_renewed(client, configured):
    connect()
    query("UPDATE ebay_accounts SET access_expires_at='2020-01-01T00:00:00+00:00'")
    assert client.get("/api/ebay/policies").get_json()["payment"] == [{"id": "payment-1", "name": "payment policy"}]
    assert query("SELECT access_token FROM ebay_accounts")[0]["access_token"] == "token-refresh_token"


def test_the_secret_never_leaves_the_server(admin, configured):
    config = admin.get("/api/admin/ebay").get_json()
    assert "client_secret" not in config and config["client_secret_set"] is True
    assert config["callback_url"].endswith("/ebay/callback")
    admin.post("/api/admin/ebay", json={"client_id": "app2", "client_secret": ""})
    assert deckledger.ebay.ebay_config(deckledger.web.sqlite3.connect(deckledger.config.DB_PATH))["client_secret"] == "secret"


# ---- Drafts -------------------------------------------------------------------------------------

def test_drafts_follow_the_preset(client):
    observe(EMBER8, "cardmarket", 4.32, "2026-09-01T06:00:00+00:00")
    client.post("/api/collection", json={"variant_id": EMBER8, "delta": 3, "condition": "Excellent"})
    preset = client.put("/api/ebay/preset", json={"game_id": "vcard", "title_template": "{name} {set_code}-{number} {finish} [{language}]", "price_factor": "90",
                                                    "price_rounding": "up99", "quantity": "owned", "aspects": "Spiel: {game}\nLeer: {nichts}"}).get_json()
    assert (preset["price_factor"], preset["category_id"]) == (90.0, "183454")
    created = client.post("/api/ebay/drafts", json={"variant_ids": [EMBER8, EMBER9]})
    assert created.status_code == 201 and created.get_json()["created"] == 2
    drafts = {draft["variant_id"]: draft for draft in client.get("/api/ebay/drafts?game_id=vcard").get_json()["drafts"]}
    ember = drafts[EMBER8]
    assert ember["title"] == "Ember (PL8) 1-001 [EN]"
    assert (ember["price"], ember["quantity"], ember["condition"]) == (3.99, 3, "400011")
    assert ember["aspects"] == [["Spiel", "VCard"]]
    assert (drafts[EMBER9]["price"], drafts[EMBER9]["quantity"], drafts[EMBER9]["condition"]) == (None, 1, "400010")
    # A card with an open draft gets no second one.
    assert client.post("/api/ebay/drafts", json={"variant_ids": [EMBER8]}).get_json() == {"created": 0, "skipped": 1, "ids": []}


def test_drafts_from_a_sheet_take_its_counts_and_prices(client):
    sheet_id = client.post("/api/trade-sheets", json={"game_id": "vcard", "name": "Verkauf"}).get_json()["id"]
    client.post(f"/api/trade-sheets/{sheet_id}/cards", json={"entries": [{"variant_id": EMBER8, "quantity": 2, "label": "4,50 €"}, {"variant_id": TIDE8, "quantity": 1}]})
    client.put("/api/ebay/preset", json={"game_id": "vcard", "price_fallback": "2"})
    assert client.post("/api/ebay/drafts", json={"sheet_id": sheet_id}).get_json()["created"] == 2
    drafts = {draft["variant_id"]: draft for draft in client.get("/api/ebay/drafts").get_json()["drafts"]}
    assert (drafts[EMBER8]["price"], drafts[EMBER8]["quantity"], drafts[EMBER8]["sheet_id"]) == (4.5, 2, sheet_id)
    assert drafts[TIDE8]["price"] == 2.0


def test_editing_a_draft(client):
    draft_id = client.post("/api/ebay/drafts", json={"variant_ids": [EMBER8]}).get_json()["ids"][0]
    url = f"/api/ebay/drafts/{draft_id}"
    updated = client.patch(url, json={"title": "  Ember   PL8 ", "price": "3,5", "quantity": 0, "condition": "400012"}).get_json()
    assert (updated["title"], updated["price"], updated["quantity"], updated["condition"]) == ("Ember PL8", 3.5, 1, "400012")
    assert client.patch(url, json={"price": "-1"}).status_code == 400
    assert login(ADMIN).patch(url, json={"title": "x"}).status_code == 404
    assert client.delete(url).get_json() == {"deleted": True}


def ready_draft(client, monkeypatch, tmp_path):
    connect()
    image = tmp_path / "card.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
    monkeypatch.setattr(deckledger.ebay, "card_image", lambda row, variant_id: (image, "image/png", "key"))
    client.put("/api/ebay/preset", json={"game_id": "vcard", "fulfillment_policy_id": "fulfillment-1", "payment_policy_id": "payment-1",
                                          "return_policy_id": "return-1", "postal_code": "10115", "price_fallback": "2,50"})
    return client.post("/api/ebay/drafts", json={"variant_ids": [EMBER8]}).get_json()["ids"][0]


def test_publishing_a_draft(client, configured, monkeypatch, tmp_path):
    draft_id = ready_draft(client, monkeypatch, tmp_path)
    assert client.post(f"/api/ebay/drafts/{draft_id}/verify").get_json() == {"ok": True, "fees": 0.35, "warnings": []}
    published = client.post(f"/api/ebay/drafts/{draft_id}/publish").get_json()
    assert published["item_id"] == "555000111222" and published["url"] == "https://www.ebay.de/itm/555000111222"
    sent = [call for call in configured.calls if call[2].get("headers", {}).get("X-EBAY-API-CALL-NAME") == "AddFixedPriceItem"][0][2]
    document = sent["data"].decode()
    assert sent["headers"]["X-EBAY-API-SITEID"] == "77" and sent["headers"]["X-EBAY-API-IAF-TOKEN"] == "token"
    for part in ("<ConditionID>4000</ConditionID>", "<Name>40001</Name><Value>400010</Value>", "<ShippingProfileID>fulfillment-1</ShippingProfileID>",
                 '<StartPrice currencyID="EUR">2.50</StartPrice>', "<PictureURL>https://i.ebayimg.com/card.jpg</PictureURL>", f"<SKU>DL-{EMBER8}</SKU>",
                 "<PostalCode>10115</PostalCode>", "<Site>Germany</Site>"):
        assert part in document
    draft = client.get("/api/ebay/drafts").get_json()["drafts"][0]
    assert (draft["status"], draft["item_id"], draft["fees"]) == ("published", "555000111222", 0.35)
    listing_row = query("SELECT * FROM ebay_listings")[0]
    assert (listing_row["variant_id"], listing_row["status"]) == (EMBER8, "active")
    assert client.post(f"/api/ebay/drafts/{draft_id}/publish").status_code == 400
    assert client.patch(f"/api/ebay/drafts/{draft_id}", json={"title": "x"}).status_code == 400


def test_a_draft_missing_its_shipping_is_not_sent(client, configured):
    connect()
    draft_id = client.post("/api/ebay/drafts", json={"variant_ids": [EMBER8]}).get_json()["ids"][0]
    response = client.post(f"/api/ebay/drafts/{draft_id}/publish")
    assert response.status_code == 400
    assert "Preis fehlt" in response.get_json()["error"] and "Versandart" in response.get_json()["error"]
    client.put("/api/ebay/preset", json={"game_id": "vcard", "shipping_mode": "policies"})
    assert "Versand, Zahlung, Rücknahme" in client.post(f"/api/ebay/drafts/{draft_id}/publish").get_json()["error"]
    assert not [call for call in configured.calls if call[1].endswith("/ws/api.dll")]


def test_without_business_policies_shipping_goes_into_the_listing(client, configured, monkeypatch, tmp_path):
    draft_id = ready_draft(client, monkeypatch, tmp_path)
    preset = client.put("/api/ebay/preset", json={"game_id": "vcard", "shipping_mode": "direct", "shipping_service": "DE_DeutschePostBrief",
                                                   "shipping_cost": "1,60", "shipping_additional_cost": "0,20", "dispatch_days": 1,
                                                   "postal_code": "10115", "price_fallback": "2,50"}).get_json()
    assert (preset["shipping_mode"], preset["shipping_cost"], preset["returns_accepted"]) == ("direct", 1.6, False)
    other = client.get("/api/ebay/preset?game_id=lorcana").get_json()
    assert other["shipping_service"] == "DE_DeutschePostBrief", "shipping holds for every game"
    client.post(f"/api/ebay/drafts/{draft_id}/publish")
    document = [call for call in configured.calls if call[2].get("headers", {}).get("X-EBAY-API-CALL-NAME") == "AddFixedPriceItem"][0][2]["data"].decode()
    for part in ("<ShippingService>DE_DeutschePostBrief</ShippingService>", '<ShippingServiceCost currencyID="EUR">1.60</ShippingServiceCost>',
                 '<ShippingServiceAdditionalCost currencyID="EUR">0.20</ShippingServiceAdditionalCost>', "<DispatchTimeMax>1</DispatchTimeMax>",
                 "<ReturnsAcceptedOption>ReturnsNotAccepted</ReturnsAcceptedOption>"):
        assert part in document
    assert "SellerProfiles" not in document


def test_an_account_without_business_policies_gets_none_to_pick(client, configured):
    connect()
    real = configured.__call__

    def not_opted_in(method, url, **kwargs):
        if "/sell/account/v1/" in url:
            return Response(400, {"errors": [{"errorId": 20403, "message": "User is not eligible for Business Policy."}]})
        return real(method, url, **kwargs)
    deckledger.ebay.http = not_opted_in
    assert client.get("/api/ebay/policies").get_json() == {"available": False, "fulfillment": [], "payment": [], "return": []}


def test_ebays_refusal_is_kept_on_the_draft(client, configured, monkeypatch, tmp_path):
    draft_id = ready_draft(client, monkeypatch, tmp_path)
    configured.fail["AddFixedPriceItem"] = "Die Kategorie ist ungültig."
    response = client.post(f"/api/ebay/drafts/{draft_id}/publish")
    assert response.status_code == 502 and "Die Kategorie ist ungültig." in response.get_json()["error"]
    draft = client.get("/api/ebay/drafts").get_json()["drafts"][0]
    assert draft["status"] == "failed" and "ungültig" in draft["error"]
    assert client.patch(f"/api/ebay/drafts/{draft_id}", json={"price": "3"}).get_json()["status"] == "draft"


def test_publishing_needs_a_connected_account(client, configured):
    client.put("/api/ebay/preset", json={"game_id": "vcard", "fulfillment_policy_id": "f", "payment_policy_id": "p", "return_policy_id": "r", "postal_code": "1", "price_fallback": "1"})
    draft_id = client.post("/api/ebay/drafts", json={"variant_ids": [EMBER8]}).get_json()["ids"][0]
    assert "kein eBay-Konto verbunden" in client.post(f"/api/ebay/drafts/{draft_id}/verify").get_json()["error"]


# ---- The account's listings ---------------------------------------------------------------------

def item_xml(item_id, title, price, quantity=1, sold=0, sku=""):
    return (f"<Item><ItemID>{item_id}</ItemID><Title>{title}</Title><SKU>{sku}</SKU><Quantity>{quantity}</Quantity>"
            f'<SellingStatus><CurrentPrice currencyID="EUR">{price}</CurrentPrice><QuantitySold>{sold}</QuantitySold></SellingStatus>'
            f"<ListingDetails><StartTime>2026-09-01T10:00:00.000Z</StartTime><ViewItemURL>https://www.ebay.de/itm/{item_id}</ViewItemURL></ListingDetails>"
            "<WatchCount>3</WatchCount></Item>")


def test_syncing_the_accounts_listings(client, configured):
    connect()
    query("INSERT INTO ebay_listings(user_id,item_id,title,status,synced_at) VALUES(1,'999','Long gone','active','t')")
    configured.selling = {
        "ActiveList": f"<ActiveList><ItemArray>{item_xml('111', 'Ember PL8', '4.99', 3, 1, f'DL-{EMBER8}')}</ItemArray>"
                      "<PaginationResult><TotalNumberOfPages>1</TotalNumberOfPages></PaginationResult></ActiveList>",
        "UnsoldList": f"<UnsoldList><ItemArray>{item_xml('222', 'Tide PL8', '2.00')}</ItemArray></UnsoldList>",
        "SoldList": "<SoldList><OrderTransactionArray><OrderTransaction><Transaction>"
                    f"{item_xml('333', 'Ember PL9', '9.50')}<QuantityPurchased>1</QuantityPurchased><TransactionID>t-1</TransactionID>"
                    '<TotalTransactionPrice currencyID="EUR">9.50</TotalTransactionPrice><CreatedDate>2026-09-20T12:00:00.000Z</CreatedDate>'
                    "<Buyer><UserID>sammlerin</UserID></Buyer></Transaction></OrderTransaction></OrderTransactionArray></SoldList>",
    }
    assert client.post("/api/ebay/listings/sync").get_json() == {"synced": True, "active": 1, "sold": 1, "unsold": 1}
    listings = {row["item_id"]: row for row in client.get("/api/ebay/listings?game_id=vcard").get_json()["listings"]}
    assert (listings["111"]["status"], listings["111"]["variant_id"], listings["111"]["quantity_sold"], listings["111"]["watch_count"]) == ("active", EMBER8, 1, 3)
    assert (listings["222"]["status"], listings["333"]["status"], listings["999"]["status"]) == ("unsold", "sold", "ended")
    sale = client.get("/api/ebay/listings").get_json()["sales"][0]
    assert (sale["buyer"], sale["price"], sale["quantity"]) == ("sammlerin", 9.5, 1)
    # A second sync does not count the sale again.
    assert client.post("/api/ebay/listings/sync").get_json()["sold"] == 0
    assert client.patch("/api/ebay/listings/222", json={"variant_id": TIDE8}).get_json() == {"linked": True}
    assert query("SELECT variant_id FROM ebay_listings WHERE item_id='222'")[0]["variant_id"] == TIDE8


def test_listings_of_other_users_stay_private(client, configured):
    connect()
    query("INSERT INTO ebay_listings(user_id,item_id,title,status,synced_at) VALUES(1,'111','Mine','active','t')")
    assert login(ADMIN).get("/api/ebay/listings").get_json()["listings"] == []
    assert login(ADMIN).patch("/api/ebay/listings/111", json={"variant_id": None}).status_code == 404


def test_each_game_has_its_own_preset_and_shares_the_shipping(client):
    client.put("/api/ebay/preset", json={"game_id": "vcard", "title_template": "V {name}", "postal_code": "10115", "payment_policy_id": "p-1"})
    lorcana = client.get("/api/ebay/preset?game_id=lorcana").get_json()
    assert (lorcana["title_template"], lorcana["postal_code"], lorcana["payment_policy_id"]) == (
        "{name} {set_code} {number} {finish} {language} {game}", "10115", "p-1")
    assert (lorcana["game_label"], lorcana["manufacturer"], lorcana["surface_foil"]) == ("Disney Lorcana", "Ravensburger", "Foil")
    assert client.get("/api/ebay/preset?game_id=vcard").get_json()["title_template"] == "V {name}"
    assert client.get("/api/ebay/preset?game_id=nope").status_code == 404


def test_the_item_specifics_are_filled_in(client):
    from conftest import EMBER8_HOLO
    query("UPDATE sets SET release_date='2024-06-01' WHERE id='vcard-test'")
    query("""UPDATE variants SET finish='1st Edition Holo' WHERE id=?""", (EMBER8_HOLO,))
    client.post("/api/collection", json={"variant_id": EMBER8_HOLO, "delta": 1})
    client.post("/api/ebay/drafts", json={"variant_ids": [EMBER8_HOLO, EMBER8]})
    drafts = {draft["variant_id"]: dict(draft["aspects"]) for draft in client.get("/api/ebay/drafts").get_json()["drafts"]}
    holo = drafts[EMBER8_HOLO]
    assert holo == {
        "Spiel": "VCard", "Edition": "Test Set", "Kartenname": "Ember (PL8)", "Character": "Ember", "Seltenheit": "Uncommon",
        "Hersteller": "Gamer Supps", "Besonderheiten": "1st Edition", "Oberflächeneffekt": "Holo", "Sprache": "Englisch",
        "Herstellungsjahr": "2024", "Kartenzustand": "Near Mint oder besser", "Bewertet": "Nein",
    }
    # Without a feature the line is left out rather than sent empty.
    assert "Besonderheiten" not in drafts[EMBER8] and drafts[EMBER8]["Oberflächeneffekt"] == "Normal"


def test_descriptions_may_be_html(client):
    query("UPDATE card_identities SET canonical_name='Ember <PL8>' WHERE id='vcard-card-ember8'")
    client.put("/api/ebay/preset", json={"game_id": "vcard", "description_template": "<p><b>{name}</b></p>"})
    client.post("/api/ebay/drafts", json={"variant_ids": [EMBER8]})
    description = client.get("/api/ebay/drafts").get_json()["drafts"][0]["description"]
    assert description == "<p><b>Ember &lt;PL8&gt;</b></p>"
    assert deckledger.ebay.description_html(description) == description
    assert deckledger.ebay.description_html("Zeile 1\nA & B") == "Zeile 1<br>A &amp; B"


# ---- Marketplace account deletion ---------------------------------------------------------------

def deletion_notice(username):
    return json.dumps({"metadata": {"topic": "MARKETPLACE_ACCOUNT_DELETION", "schemaVersion": "1.0"},
                       "notification": {"notificationId": "n-1", "data": {"username": username, "userId": "u-1", "eiasToken": "e"}}},
                      separators=(",", ":")).encode()


def signed(body, key):
    import base64
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    signature = base64.b64encode(key.sign(body, ec.ECDSA(hashes.SHA1()))).decode()
    return base64.b64encode(json.dumps({"alg": "ECDSA", "kid": "key-1", "signature": signature, "digest": "SHA1"}).encode()).decode()


@pytest.fixture
def ebay_key(configured):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    # eBay sends the key on one line.
    configured.public_key = pem.replace("\n", "")
    deckledger.ebay.PUBLIC_KEYS.clear()
    return key


def test_the_deletion_endpoint_answers_ebays_challenge(admin):
    import hashlib
    config = admin.get("/api/admin/ebay").get_json()
    token, endpoint = config["deletion_token"], config["deletion_endpoint"]
    assert re.fullmatch(r"[A-Za-z0-9_-]{32,80}", token) and endpoint == "http://localhost/ebay/account-deletion"
    response = deckledger.app.test_client().get("/ebay/account-deletion?challenge_code=abc123")
    assert response.status_code == 200 and response.is_json
    assert response.get_json() == {"challengeResponse": hashlib.sha256(f"abc123{token}{endpoint}".encode()).hexdigest()}
    assert admin.get("/api/admin/ebay").get_json()["deletion_token"] == token


def test_deletions_of_unknown_accounts_are_only_acknowledged(configured):
    calls = len(configured.calls)
    response = deckledger.app.test_client().post("/ebay/account-deletion", data=deletion_notice("someone"), content_type="application/json")
    assert response.status_code == 204 and len(configured.calls) == calls


def test_a_deleted_account_is_forgotten(ebay_key):
    connect()
    query("INSERT INTO ebay_sales(user_id,item_id,transaction_id,buyer) VALUES(1,'1','t1','kartenhai'),(1,'2','t2','other')")
    body = deletion_notice("kartenhai")
    anonymous = deckledger.app.test_client()
    forged = anonymous.post("/ebay/account-deletion", data=body, content_type="application/json", headers={"X-EBAY-SIGNATURE": signed(b"{}", ebay_key)})
    assert forged.status_code == 412 and query("SELECT COUNT(*) AS n FROM ebay_accounts")[0]["n"] == 1
    response = anonymous.post("/ebay/account-deletion", data=body, content_type="application/json", headers={"X-EBAY-SIGNATURE": signed(body, ebay_key)})
    assert response.status_code == 204
    assert query("SELECT COUNT(*) AS n FROM ebay_accounts")[0]["n"] == 0
    assert [row["buyer"] for row in query("SELECT buyer FROM ebay_sales ORDER BY item_id")] == ["", "other"]


def test_a_listing_with_variations_needs_the_chosen_one(client, configured):
    configured.groups = {ITEM}
    configured.items = {f"{ITEM}-526719127153": listing(4)}
    response = client.post(f"/api/variants/{EMBER8}/ebay-items", json={"url": f"https://www.ebay.de/itm/{ITEM}"})
    assert "mehrere Varianten" in response.get_json()["items"][0]["error"]
    response = client.post(f"/api/variants/{EMBER8}/ebay-items", json={"url": f"https://www.ebay.de/itm/{ITEM}?hash=x&var=526719127153"})
    item = next(item for item in response.get_json()["items"] if item["item_id"] == f"{ITEM}-526719127153")
    assert (item["status"], item["price"]) == ("active", 4.0)
    assert price_of(client, EMBER8, "vcard-card-ember8")["price"] == 4.0



# ---- Listing statistics, suggestions and sheets that follow eBay ----------------------------------

ANALYTICS = deckledger.ebay.ANALYTICS_SCOPE


def own_listing(item_id, variant_id=None, title="Ember PL8", price=4.99, quantity=1, sold=0, status="active", watchers=0):
    query("""INSERT INTO ebay_listings(user_id,item_id,variant_id,title,price,currency,quantity,quantity_sold,status,watch_count,started_at,synced_at)
             VALUES(1,?,?,?,?,'EUR',?,?,?,?,'2026-09-01T00:00:00+00:00','t')""", (item_id, variant_id, title, price, quantity, sold, status, watchers))


def test_views_come_from_the_traffic_report_once_it_is_granted(client, configured):
    connect()
    configured.selling = {"ActiveList": f"<ActiveList><ItemArray>{item_xml('111', 'Ember PL8', '4.99', 3, 0, '')}</ItemArray></ActiveList>",
                          "UnsoldList": "<UnsoldList/>", "SoldList": "<SoldList/>"}
    configured.traffic = {"111": [120, 4000, 2.5, 1.2]}
    client.post("/api/ebay/listings/sync")
    assert configured.traffic_params is None, "an account connected before stays without views"
    assert client.get("/api/ebay/status").get_json()["stats_need_reconnect"] is True
    query("UPDATE ebay_accounts SET scopes=?", (" ".join(deckledger.ebay.USER_SCOPES),))
    client.post("/api/ebay/listings/sync")
    assert "listing_ids:{111}" in configured.traffic_params["filter"] and "marketplace_ids:{EBAY_DE}" in configured.traffic_params["filter"]
    listing = client.get("/api/ebay/listings").get_json()["listings"][0]
    assert (listing["view_count"], listing["impression_count"], listing["click_through_rate"], listing["watch_count"]) == (120, 4000, 2.5, 3)
    assert (listing["popularity"], listing["rank"]) == (120 + 30 + 40, 1)
    assert client.get("/api/ebay/status").get_json()["stats_need_reconnect"] is False


def test_a_renewed_token_asks_only_for_what_was_granted(client, configured):
    connect()
    query("UPDATE ebay_accounts SET access_expires_at='2000-01-01T00:00:00+00:00'")
    with deckledger.app.app_context():
        deckledger.ebay.user_token(deckledger.ebay.db(), 1)
    form = [call[2]["data"] for call in configured.calls if call[1].endswith("/identity/v1/oauth2/token")][-1]
    assert ANALYTICS not in form["scope"] and "sell.inventory" in form["scope"]


def test_suggestions_come_from_the_collection_and_read_long_titles(client):
    from conftest import EMBER8_HOLO
    for variant in (EMBER8, EMBER8_HOLO, EMBER9):
        client.post("/api/collection", json={"variant_id": variant, "delta": 1})
    own_listing("111", title="VCard Ember PL8 Test Set Holo Near Mint TCG Karte Sammlerstück")
    suggested = client.get("/api/ebay/listings/111/suggestions").get_json()
    assert [card["variant_id"] for card in suggested][:2] == [EMBER8_HOLO, EMBER8]
    assert EMBER9 not in {card["variant_id"] for card in suggested}, "PL9 is not PL8"
    assert TIDE8 not in {card["variant_id"] for card in suggested}, "not in the collection"
    found = client.get("/api/search", query_string={"q": "tide", "owned": 1}).get_json()
    assert found == []
    assert client.get("/api/ebay/listings/nope/suggestions").status_code == 404


def test_sale_sheets_follow_the_listings_of_their_cards(client):
    connect()
    for variant in (EMBER8, EMBER9, TIDE8):
        client.post("/api/collection", json={"variant_id": variant, "delta": 3})
    sheet = client.post("/api/trade-sheets", json={"game_id": "vcard", "name": "Verkauf", "kind": "WTS"}).get_json()["id"]
    trade = client.post("/api/trade-sheets", json={"game_id": "vcard", "name": "Tausch", "kind": "WTT"}).get_json()["id"]
    for target in (sheet, trade):
        client.post(f"/api/trade-sheets/{target}/cards", json={"entries": [{"variant_id": EMBER8, "quantity": 1}, {"variant_id": EMBER9, "quantity": 2},
                                                                         {"variant_id": TIDE8, "quantity": 1, "label": "3 €"}]})
    own_listing("111", title="Ember PL8", price=4.5, quantity=3, sold=1, watchers=4)
    own_listing("222", title="Ember PL9", price=9.0, quantity=2)
    client.patch("/api/ebay/listings/111", json={"variant_id": EMBER8})
    client.patch("/api/ebay/listings/222", json={"variant_id": EMBER9})
    cards = {card["variant_id"]: card for card in client.get(f"/api/trade-sheets/{sheet}").get_json()["cards"]}
    assert (cards[EMBER8]["quantity"], cards[EMBER8]["label"]) == (2, "4,50 €")
    assert (cards[EMBER8]["ebay"]["available"], cards[EMBER8]["ebay"]["watchers"]) == (2, 4)
    assert (cards[TIDE8]["label"], cards[TIDE8]["ebay"]) == ("3 €", None), "a card without a listing keeps what it had"
    untouched = {card["variant_id"]: card for card in client.get(f"/api/trade-sheets/{trade}").get_json()["cards"]}
    assert (untouched[EMBER8]["quantity"], untouched[EMBER8]["label"]) == (1, ""), "a trade sheet has no prices to follow"
    # Sold out: off the sheet. Ended unsold: stays, without the eBay price.
    query("UPDATE ebay_listings SET status='sold',quantity_sold=3 WHERE item_id='111'")
    query("UPDATE ebay_listings SET status='unsold' WHERE item_id='222'")
    with deckledger.app.app_context():
        deckledger.ebay.update_sheets_from_listings(deckledger.ebay.db(), 1)
        deckledger.ebay.db().commit()
    cards = {card["variant_id"]: card for card in client.get(f"/api/trade-sheets/{sheet}").get_json()["cards"]}
    assert EMBER8 not in cards and (cards[EMBER9]["quantity"], cards[EMBER9]["label"]) == (2, "")


def test_a_sheet_can_stop_following_ebay(client):
    connect()
    client.post("/api/collection", json={"variant_id": EMBER8, "delta": 3})
    sheet = client.post("/api/trade-sheets", json={"game_id": "vcard", "name": "Verkauf", "kind": "WTS"}).get_json()["id"]
    client.post(f"/api/trade-sheets/{sheet}/cards", json={"variant_id": EMBER8, "quantity": 1})
    assert client.patch(f"/api/trade-sheets/{sheet}", json={"ebay_sync": False}).get_json()["sheet"]["ebay_sync"] == 0
    own_listing("111", variant_id=EMBER8, price=4.5, quantity=3)
    client.patch("/api/ebay/listings/111", json={"variant_id": EMBER8})
    card = client.get(f"/api/trade-sheets/{sheet}").get_json()["cards"][0]
    assert (card["quantity"], card["label"]) == (1, "") and card["ebay"]["price"] == 4.5, "the listing still shows, nothing is taken over"
    card = client.patch(f"/api/trade-sheets/{sheet}", json={"ebay_sync": True}).get_json()["cards"][0]
    assert (card["quantity"], card["label"]) == (3, "4,50 €"), "switching it on catches up at once"


def test_cards_on_ebay_get_a_badge_on_the_sheet(client, monkeypatch):
    import sheet_render
    drawn = []
    real = sheet_render.ebay_badge
    monkeypatch.setattr(sheet_render, "ebay_badge", lambda width: drawn.append(width) or real(width))
    client.post("/api/collection", json={"variant_id": EMBER8, "delta": 1})
    sheet = client.post("/api/trade-sheets", json={"game_id": "vcard", "name": "Verkauf", "kind": "WTS"}).get_json()["id"]
    client.post(f"/api/trade-sheets/{sheet}/cards", json={"entries": [{"variant_id": EMBER8, "quantity": 1}, {"variant_id": TIDE8, "quantity": 1}]})
    own_listing("111", variant_id=EMBER8)
    assert client.get(f"/api/trade-sheets/{sheet}/image/1.jpg?scale=0.3").status_code == 200
    assert len(drawn) == 1

