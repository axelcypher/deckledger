"""User management by an admin, and what happens to a session once its account is gone."""
from conftest import ADMIN, EMBER8, deckledger, login, query

NEW = {"username": "mia", "display_name": "Mia", "email": "mia@example.com", "password": "geheim-1234", "role": "user"}


def create(admin, **overrides):
    return admin.post("/api/admin/users", json={**NEW, **overrides})


def test_listing_shows_accounts_without_password_hashes(admin):
    users = admin.get("/api/admin/users").get_json()
    assert [(user["username"], user["role"], user["is_self"]) for user in users] == [("admin", "admin", True), ("demo", "user", False)]
    assert all("password_hash" not in user for user in users)


def test_user_management_is_admin_only(client, anonymous):
    assert client.get("/api/admin/users").status_code == 403
    assert client.post("/api/admin/users", json=NEW).status_code == 403
    assert anonymous.get("/api/admin/users").status_code == 401


def test_created_account_can_sign_in_and_use_the_app(admin):
    assert create(admin).status_code == 201
    mia = login(("mia", "geheim-1234"))
    games = mia.get("/api/bootstrap").get_json()
    assert games["user"]["display_name"] == "Mia" and games["user"]["role"] == "user"
    # Regression: accounts made while the app runs had no default lists until the next restart.
    assert mia.post("/api/watchlist", json={"variant_id": EMBER8}).get_json()["active"] is True
    assert {item["name"] for item in mia.get("/api/watchlists?game_id=vcard").get_json()} == {"Merkliste"}


def test_creation_validates_its_input(admin):
    for overrides, text in (
        ({"username": "x"}, "3-32 Zeichen"), ({"username": "DEMO"}, "bereits vergeben"), ({"password": "kurz"}, "mindestens 8"),
        ({"email": "keine-mail"}, "ungültig"), ({"role": "root"}, "Rolle"), ({"password": ""}, "erforderlich"),
    ):
        response = create(admin, **overrides)
        assert response.status_code == 400 and text in response.get_json()["error"], overrides
    assert query("SELECT COUNT(*) n FROM users")[0]["n"] == 2


def test_edit_role_and_reset_password(admin):
    user_id = create(admin).get_json()["id"]
    assert admin.patch(f"/api/admin/users/{user_id}", json={"role": "admin", "display_name": "Mia M.", "password": "neues-passwort"}).status_code == 200
    mia = login(("mia", "neues-passwort"))
    assert mia.get("/api/admin/users").status_code == 200
    assert deckledger.app.test_client().post("/login", data={"username": "mia", "password": "geheim-1234"}).status_code == 401


def test_the_last_admin_cannot_be_removed_or_demoted(admin):
    admin_id = query("SELECT id FROM users WHERE username='admin'")[0]["id"]
    assert admin.patch(f"/api/admin/users/{admin_id}", json={"role": "user"}).status_code == 400
    assert admin.delete(f"/api/admin/users/{admin_id}").status_code == 400
    # With a second admin the first one may step down, but still not delete itself here.
    create(admin, role="admin")
    assert admin.delete(f"/api/admin/users/{admin_id}").status_code == 400
    assert admin.patch(f"/api/admin/users/{admin_id}", json={"role": "user"}).status_code == 200


def test_deleting_an_account_removes_its_data_and_ends_its_session(admin):
    demo = login()
    demo.post("/api/collection", json={"variant_id": EMBER8, "delta": 2})
    deck_id = demo.post("/api/decks", json={"game_id": "vcard", "name": "Weg"}).get_json()["id"]
    demo.post(f"/api/decks/{deck_id}/cards", json={"variant_id": EMBER8, "zone": "auto", "delta": 1})
    demo.post("/api/watchlist", json={"variant_id": EMBER8})
    sheet_id = demo.post("/api/trade-sheets", json={"game_id": "vcard", "name": "Weg"}).get_json()["id"]
    demo.post(f"/api/trade-sheets/{sheet_id}/cards", json={"variant_id": EMBER8, "delta": 1})
    demo.post("/api/settings", json={"activeGameId": "vcard"})
    admin.post("/api/collection", json={"variant_id": EMBER8, "delta": 5})
    demo_id = query("SELECT id FROM users WHERE username='demo'")[0]["id"]

    assert admin.delete(f"/api/admin/users/{demo_id}").get_json() == {"deleted": True}

    for table in ("collection_entries", "decks", "named_watchlists", "trade_sheets", "user_settings"):
        assert query(f"SELECT COUNT(*) n FROM {table} WHERE user_id=?", (demo_id,))[0]["n"] == 0, table
    assert query("SELECT COUNT(*) n FROM deck_cards")[0]["n"] == 0 and query("SELECT COUNT(*) n FROM trade_sheet_cards")[0]["n"] == 0
    assert query("SELECT SUM(quantity) q FROM collection_entries")[0]["q"] == 5, "other accounts keep their data"
    # The deleted account's cookie is still in its browser; it must not work any more.
    assert demo.get("/api/bootstrap").status_code == 401
    assert demo.get("/").status_code == 302


def test_the_login_page_offers_no_demo_account(anonymous):
    page = anonymous.get("/login").get_data(as_text=True)
    assert "Demo-Zugang" not in page and 'value="deckledger"' not in page
