"""Searching communities for a sheet's cards: the list of communities, which posts fit which
sheet, and the job that reads one feed at a time. Nothing here talks to Reddit."""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from conftest import ADMIN, EMBER8, EMBER9, TIDE8, deckledger, login, query
from test_watcher import POST_URL, inbox, reddit, run_job, sheet  # noqa: F401  -- fixtures

watcher = deckledger.watcher


def listed(external_id, author, title, text="see pictures", age_hours=1):
    posted = (datetime.now(timezone.utc) - timedelta(hours=age_hours)).replace(microsecond=0).isoformat()
    title = title.replace("&", "&amp;")
    return f"""<entry><author><name>/u/{author}</name></author><category term="vcardtrades" label="r/vcardtrades"/>
      <content type="html">&lt;!-- SC_OFF --&gt;&lt;div class="md"&gt;&lt;p&gt;{text}&lt;/p&gt;&lt;/div&gt;&lt;!-- SC_ON --&gt; &amp;#32; submitted by &amp;#32; &lt;a href="https://www.reddit.com/user/{author}"&gt; /u/{author} &lt;/a&gt; &lt;br/&gt; &lt;span&gt;&lt;a href="x"&gt;[link]&lt;/a&gt;&lt;/span&gt; &amp;#32; &lt;span&gt;&lt;a href="x"&gt;[comments]&lt;/a&gt;&lt;/span&gt;</content>
      <id>{external_id}</id><link href="https://www.reddit.com/r/vcardtrades/comments/{external_id[3:]}/post/" />
      <updated>{posted}</updated><published>{posted}</published><title>{title}</title></entry>"""


def listing(*posts):
    return ("""<?xml version="1.0" encoding="UTF-8"?><feed xmlns="http://www.w3.org/2005/Atom">
      <category term="vcardtrades" label="r/vcardtrades"/><title>newest submissions : vcardtrades</title>""" + "".join(posts) + "</feed>").encode()


def open_gate():
    query("DELETE FROM app_settings WHERE key='reddit_next_request_at'")


@pytest.fixture
def community(client):
    return client.post("/api/communities", json={"game_id": "vcard", "name": "r/VcardTrades"}).get_json()["id"]


def finds(client, **params):
    return {item["external_id"]: item for item in inbox(client, **params)["items"] if item["kind"] == "post"}


# ---- the list of communities -------------------------------------------------------------------

@pytest.mark.parametrize("typed, name", [
    ("vcardtrades", "vcardtrades"), ("r/VcardTrades", "vcardtrades"), ("/r/vcard_trades/", "vcard_trades"),
    ("https://www.reddit.com/r/vcardtrades/", "vcardtrades"), ("old.reddit.com/r/vcardtrades", "vcardtrades"),
    ("", ""), ("r/a", ""), ("two words", ""), ("https://example.com/r/vcardtrades", ""),
    ("https://www.reddit.com/r/vcardtrades/comments/1abc23/post/", ""),
])
def test_how_a_community_may_be_typed(typed, name):
    assert watcher.community_name(typed) == name


def test_communities_are_kept_per_account_and_game(client, community):
    assert [(row["name"], row["game_id"], row["last_checked_at"]) for row in client.get("/api/communities?game_id=vcard").get_json()] == [("vcardtrades", "vcard", None)]
    assert client.get("/api/communities?game_id=lorcana").get_json() == []
    assert client.post("/api/communities", json={"game_id": "vcard", "name": "vcardtrades"}).status_code == 409
    assert client.post("/api/communities", json={"game_id": "vcard", "name": "not a name"}).status_code == 400
    assert client.post("/api/communities", json={"game_id": "nope", "name": "vcardtrades"}).status_code == 404
    assert client.post("/api/communities", json={"game_id": "lorcana", "name": "vcardtrades"}).status_code == 201, "the same community for another game"

    admin = login(ADMIN)
    assert admin.get("/api/communities").get_json() == []
    assert admin.delete(f"/api/communities/{community}").status_code == 404
    assert admin.post(f"/api/communities/{community}/check").status_code == 404

    for number in range(watcher.COMMUNITIES_PER_GAME - 1):
        assert client.post("/api/communities", json={"game_id": "vcard", "name": f"sub{number}"}).status_code == 201
    assert client.post("/api/communities", json={"game_id": "vcard", "name": "onemore"}).status_code == 400


def test_a_feed_is_forgotten_with_its_last_reader(client, community):
    other = login(ADMIN).post("/api/communities", json={"game_id": "vcard", "name": "vcardtrades"}).get_json()["id"]
    assert client.delete(f"/api/communities/{community}").get_json() == {"deleted": True}
    assert query("SELECT COUNT(*) n FROM community_feeds")[0]["n"] == 1, "somebody else still reads it"
    login(ADMIN).delete(f"/api/communities/{other}")
    assert query("SELECT COUNT(*) n FROM community_feeds")[0]["n"] == 0


# ---- what a post offers and what it asks for ---------------------------------------------------

@pytest.mark.parametrize("title, expected", [
    ("[US,US] [H] Ember PL8, Tide [W] PayPal G&S", [("has", " Ember PL8, Tide "), ("wants", " PayPal G&S"), ("mentions", "body")]),
    ("[us][w]Ember PL9[h]paypal", [("wants", "Ember PL9"), ("has", "paypal"), ("mentions", "body")]),
    ("[WTS] Ember PL8", [("has", "[WTS] Ember PL8\nbody")]),
    ("WTB: Ember PL9", [("wants", "WTB: Ember PL9\nbody")]),
    ("Looking for Ember", [("wants", "Looking for Ember\nbody")]),
    ("WTS/WTB Ember", [("mentions", "WTS/WTB Ember\nbody")]),
    ("My Ember collection", [("mentions", "My Ember collection\nbody")]),
])
def test_which_part_of_a_post_says_what(title, expected):
    assert watcher.post_sides(title, "body") == expected


def test_feed_trailer_is_not_part_of_a_post():
    posts = watcher.parse_listing(listing(listed("t3_aaa", "seller", "[H] Ember & more [W] PayPal", "Ember PL8 here")))
    assert [(post["external_id"], post["author"], post["title"], post["body"]) for post in posts] == [("t3_aaa", "seller", "[H] Ember & more [W] PayPal", "Ember PL8 here")]
    assert posts[0]["url"].endswith("/comments/aaa/post/")


# ---- which posts fit which sheet ---------------------------------------------------------------

POSTS = listing(
    listed("t3_want", "buyer", "[US] [H] PayPal [W] Ember PL8, Tide"),
    listed("t3_have", "seller", "[US] [H] Ember PL9 [W] PayPal"),
    listed("t3_wtb", "buyer2", "[WTB] Ember (PL9)"),
    listed("t3_talk", "collector", "Mail day", "my Tide PL8 arrived"),
    listed("t3_mine", "demo_seller", "[H] PayPal [W] Ember PL8"),
    listed("t3_old", "buyer3", "[H] PayPal [W] Ember PL8", age_hours=24 * (watcher.FIND_DAYS + 1)),
    listed("t3_bot", "AutoModerator", "Weekly thread: [W] Ember PL8"),
    listed("t3_none", "other", "[H] Spark PL8 [W] PayPal"),
)


def test_an_offer_sheet_finds_who_wants_its_cards(client, sheet, community, reddit):
    client.post("/api/settings", json={"redditUsername": "demo_seller"})
    reddit.payload = POSTS
    assert client.post(f"/api/communities/{community}/check").get_json()["new"] == 3
    assert reddit.asked == ["https://www.reddit.com/r/vcardtrades/new/.rss?limit=100"]
    found = finds(client)
    assert set(found) == {"t3_want", "t3_wtb", "t3_talk"}, "not: what others have, own posts, old posts, the bot"
    wanted = found["t3_want"]
    assert (wanted["sheet_id"], wanted["author"], wanted["community"], wanted["title"]) == (sheet, "buyer", "vcardtrades", "[US] [H] PayPal [W] Ember PL8, Tide")
    assert {(match["variant_id"], match["certain"], match["side"]) for match in wanted["matches"]} == {(EMBER8, True, "wants"), (TIDE8, False, "wants")}
    assert [(match["variant_id"], match["side"]) for match in found["t3_wtb"]["matches"]] == [(EMBER9, "wants")]
    assert [(match["variant_id"], match["side"]) for match in found["t3_talk"]["matches"]] == [(TIDE8, "mentions")]
    assert client.get("/api/communities?game_id=vcard").get_json()[0]["last_checked_at"]


def test_a_wanted_sheet_finds_who_has_its_cards(client, sheet, community, reddit):
    client.patch(f"/api/trade-sheets/{sheet}", json={"kind": "WTB/WTTF"})
    reddit.payload = POSTS
    client.post(f"/api/communities/{community}/check")
    found = finds(client)
    assert set(found) == {"t3_have", "t3_talk"}
    assert [(match["variant_id"], match["side"]) for match in found["t3_have"]["matches"]] == [(EMBER9, "has")]


def test_the_title_decides_where_a_card_stands(client, sheet, community, reddit):
    # The text of a post usually repeats what its title offers or asks for.
    reddit.payload = listing(listed("t3_have", "seller", "[H] Ember PL9 [W] PayPal", "Ember PL9 is near mint. Also looking for Tide PL8"))
    client.post(f"/api/communities/{community}/check")
    assert [(match["variant_id"], match["side"]) for match in finds(client)["t3_have"]["matches"]] == [(TIDE8, "mentions")]
    client.patch(f"/api/trade-sheets/{sheet}", json={"kind": "WTB"})
    query("DELETE FROM inbox_items")
    open_gate()
    client.post(f"/api/communities/{community}/check")
    assert {(match["variant_id"], match["side"]) for match in finds(client)["t3_have"]["matches"]} == {(EMBER9, "has"), (TIDE8, "mentions")}


def test_a_post_is_reported_once_per_sheet(client, sheet, community, reddit):
    reddit.payload = listing(listed("t3_want", "buyer", "[H] PayPal [W] Ember PL8"))
    assert client.post(f"/api/communities/{community}/check").get_json()["new"] == 1
    item = inbox(client)["items"][0]["id"]
    client.patch(f"/api/inbox/{item}", json={"state": "done"})
    open_gate()
    assert client.post(f"/api/communities/{community}/check").get_json()["new"] == 0
    assert inbox(client)["items"] == [] and len(inbox(client, state="all")["items"]) == 1

    second = client.post("/api/trade-sheets", json={"game_id": "vcard", "name": "Zweites"}).get_json()["id"]
    client.post(f"/api/trade-sheets/{second}/cards", json={"variant_id": EMBER8, "delta": 1})
    open_gate()
    assert client.post(f"/api/communities/{community}/check").get_json()["new"] == 1, "the other sheet has that card too"
    assert [entry["sheet_id"] for entry in inbox(client)["items"]] == [second]


def test_a_sheet_can_stay_out_or_keep_to_some_communities(client, sheet, community, reddit):
    reddit.payload = listing(listed("t3_want", "buyer", "[H] PayPal [W] Ember PL8"))
    saved = client.patch(f"/api/trade-sheets/{sheet}", json={"scout": False}).get_json()["sheet"]
    assert (saved["scout"], saved["scout_communities"]) == (0, "")
    assert client.post(f"/api/communities/{community}/check").get_json()["new"] == 0

    saved = client.patch(f"/api/trade-sheets/{sheet}", json={"scout": True, "scout_communities": ["OtherSub", "not a name", 7]}).get_json()["sheet"]
    assert (saved["scout"], saved["scout_communities"]) == (1, "othersub")
    open_gate()
    assert client.post(f"/api/communities/{community}/check").get_json()["new"] == 0, "the sheet keeps to another community"

    client.patch(f"/api/trade-sheets/{sheet}", json={"scout_communities": ["othersub", "vcardtrades"]})
    assert client.patch(f"/api/trade-sheets/{sheet}", json={"name": "Abgabe"}).get_json()["sheet"]["scout_communities"] == "othersub,vcardtrades", "other edits leave it alone"
    open_gate()
    assert client.post(f"/api/communities/{community}/check").get_json()["new"] == 1


def test_a_linked_post_of_ones_own_is_not_a_find(client, sheet, community, reddit):
    client.post(f"/api/trade-sheets/{sheet}/posts", json={"url": POST_URL})
    reddit.payload = listing(listed("t3_1abc23", "someone", "[WTB] Ember PL8"))
    assert client.post(f"/api/communities/{community}/check").get_json()["new"] == 0


def test_everybody_with_the_community_gets_their_finds_from_one_read(client, sheet, community, reddit):
    admin = login(ADMIN)
    admin.post("/api/communities", json={"game_id": "vcard", "name": "vcardtrades"})
    theirs = admin.post("/api/trade-sheets", json={"game_id": "vcard", "name": "Suche", "kind": "WTB"}).get_json()["id"]
    admin.post(f"/api/trade-sheets/{theirs}/cards", json={"variant_id": EMBER9, "delta": 1})
    reddit.payload = POSTS
    assert client.post(f"/api/communities/{community}/check").get_json()["new"] == 5
    assert len(reddit.asked) == 1
    assert set(finds(client)) == {"t3_want", "t3_wtb", "t3_talk", "t3_mine"} and set(finds(admin)) == {"t3_have"}


def test_finds_go_with_their_sheet_and_their_account(client, admin, sheet, community, reddit):
    reddit.payload = POSTS
    client.post(f"/api/communities/{community}/check")
    client.delete(f"/api/trade-sheets/{sheet}")
    assert query("SELECT COUNT(*) n FROM inbox_items")[0]["n"] == 0
    demo_id = query("SELECT id FROM users WHERE username='demo'")[0]["id"]
    assert admin.delete(f"/api/admin/users/{demo_id}").get_json() == {"deleted": True}
    assert query("SELECT COUNT(*) n FROM watch_communities")[0]["n"] == 0


# ---- reading ---------------------------------------------------------------------------------------

def test_check_now_respects_the_gate_and_notes_a_refusal(client, sheet, community, reddit):
    reddit.payload = listing()
    assert client.post(f"/api/communities/{community}/check").status_code == 200
    refused = client.post(f"/api/communities/{community}/check")
    assert refused.status_code == 429 and refused.get_json()["wait"] > 0 and len(reddit.asked) == 1
    open_gate()
    reddit.error = watcher.FeedError("Nicht (mehr) lesbar (404).", 30)
    failed = client.post(f"/api/communities/{community}/check")
    assert failed.status_code == 502 and "404" in failed.get_json()["community"]["last_error"]


def test_the_job_reads_communities_and_posts_in_turn(client, sheet, community, reddit):
    post = client.post(f"/api/trade-sheets/{sheet}/posts", json={"url": POST_URL}).get_json()["id"]
    reddit.payload = listing(listed("t3_want", "buyer", "[H] PayPal [W] Ember PL8"))
    assert run_job() == {"retired": 0, "community": "vcardtrades", "new": 1}
    open_gate()
    reddit.payload = b"<feed xmlns='http://www.w3.org/2005/Atom'/>"
    assert run_job() == {"retired": 0, "checked": post, "new": 0}
    open_gate()
    assert run_job()["checked"] == post, "the community was read less than its interval ago"
    long_ago = (datetime.now(timezone.utc) - timedelta(seconds=watcher.COMMUNITY_INTERVAL_SECONDS + 5)).replace(microsecond=0).isoformat()
    query("UPDATE community_feeds SET last_checked_at=?", (long_ago,))
    open_gate()
    assert run_job()["community"] == "vcardtrades"
    assert ["/r/" in url for url in reddit.asked] == [True, False, False, True]


def test_a_community_that_cannot_be_read_does_not_stop_the_job(client, sheet, community, reddit):
    reddit.error = watcher.FeedError("Nicht (mehr) lesbar (403).")
    summary = run_job()
    assert summary["community"] == "vcardtrades" and "403" in summary["error"]
    assert "403" in query("SELECT last_error FROM community_feeds")[0]["last_error"]


# ---- the step from the previous schema ---------------------------------------------------------

def test_inbox_entries_keep_their_sheet_through_the_rebuild(tmp_path):
    migrations = deckledger.migrations
    connection = sqlite3.connect(tmp_path / "before.db")
    connection.row_factory = sqlite3.Row
    migrations.migrate(connection, migrations=migrations.MIGRATIONS[:5], log=lambda message: None)
    connection.execute("INSERT INTO trade_sheets(id,user_id,game_id,name,created_at,updated_at) VALUES(4,1,'vcard','Abgabe','t','t')")
    connection.execute("INSERT INTO sheet_posts(id,user_id,sheet_id,source,external_id,url,community,created_at) VALUES(9,1,4,'reddit','1abc23','u','vcardtrades','t')")
    connection.execute("INSERT INTO inbox_items(id,user_id,post_id,kind,external_id,author,body,url,posted_at,matches,state,created_at) VALUES(3,1,9,'comment','t1_a','buyer','hello','link','when','[]','done','t')")
    connection.commit()
    assert migrations.migrate(connection, log=lambda message: None) == list(range(6, len(migrations.MIGRATIONS) + 1))
    row = dict(connection.execute("SELECT * FROM inbox_items").fetchone())
    assert row == {"id": 3, "user_id": 1, "sheet_id": 4, "post_id": 9, "kind": "comment", "external_id": "t1_a", "author": "buyer", "title": "",
                   "body": "hello", "url": "link", "community": "vcardtrades", "posted_at": "when", "matches": "[]", "state": "done", "created_at": "t"}
    sheet = connection.execute("SELECT scout,scout_communities FROM trade_sheets").fetchone()
    assert (sheet["scout"], sheet["scout_communities"]) == (1, "")
    assert not connection.execute("SELECT 1 FROM sqlite_master WHERE name='inbox_items_before_0006'").fetchone()
    connection.close()
