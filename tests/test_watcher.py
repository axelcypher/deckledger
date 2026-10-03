"""Watching the posts a sheet was published as: linking them, reading their comment feed, the
inbox. Nothing here talks to Reddit; the feed is handed in."""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from conftest import ADMIN, EMBER8, EMBER9, TIDE8, deckledger, login, query

watcher = deckledger.watcher
POST_URL = "https://www.reddit.com/r/vcardtrades/comments/1abc23/wts_ember_and_friends/"


def entry(external_id, author, text, minute, link="c"):
    return f"""<entry><author><name>/u/{author}</name></author>
      <content type="html">&lt;!-- SC_OFF --&gt;&lt;div class="md"&gt;&lt;p&gt;{text}&lt;/p&gt;&lt;/div&gt;&lt;!-- SC_ON --&gt;</content>
      <id>{external_id}</id><link href="https://www.reddit.com/r/vcardtrades/comments/1abc23/wts/{link}{external_id}/"/>
      <updated>2026-10-02T10:{minute:02d}:00+00:00</updated><title>/u/{author} on [WTS] Ember and friends</title></entry>"""


def feed(*comments):
    return ("""<?xml version="1.0" encoding="UTF-8"?><feed xmlns="http://www.w3.org/2005/Atom">
      <category term=" reddit.com" label="r/ reddit.com"/><category term="vcardtrades" label="r/vcardtrades"/>
      <title>[WTS] Ember and friends : reddit.com</title>
      <entry><author><name>/u/seller</name></author><content type="html">&lt;p&gt;my cards&lt;/p&gt;</content><id>t3_1abc23</id>
        <link href="https://www.reddit.com/r/vcardtrades/comments/1abc23/wts/"/><updated>2026-10-02T10:00:00+00:00</updated><title>[WTS] Ember and friends</title></entry>
      """ + "".join(comments) + "</feed>").encode()


@pytest.fixture
def reddit(monkeypatch):
    """Stands in for Reddit: tests set what the feed returns and see what was asked for."""
    state = types = type("Reddit", (), {})()
    state.payload, state.pause, state.error, state.asked = feed(), 2, None, []

    def fetch(url):
        state.asked.append(url)
        if state.error:
            raise state.error
        return state.payload, state.pause

    monkeypatch.setattr(watcher, "fetch_feed", fetch)
    return state


@pytest.fixture
def sheet(client):
    sheet_id = client.post("/api/trade-sheets", json={"game_id": "vcard", "name": "Abgabe"}).get_json()["id"]
    for variant in (EMBER8, EMBER9, TIDE8):
        client.post(f"/api/trade-sheets/{sheet_id}/cards", json={"variant_id": variant, "delta": 1})
    return sheet_id


@pytest.fixture
def post(client, sheet):
    return client.post(f"/api/trade-sheets/{sheet}/posts", json={"url": POST_URL}).get_json()["id"]


def inbox(client, **params):
    query_string = "&".join(f"{key}={value}" for key, value in params.items())
    return client.get(f"/api/inbox?{query_string}").get_json()


# ---- linking a post ----------------------------------------------------------------------------

@pytest.mark.parametrize("url, expected", [
    (POST_URL, ("reddit", "1abc23", "vcardtrades")),
    ("https://old.reddit.com/r/Some_Sub/comments/1ABC23/title/?utm=x", ("reddit", "1abc23", "Some_Sub")),
    ("https://reddit.com/comments/1abc23", ("reddit", "1abc23", "")),
    ("https://redd.it/1abc23", ("reddit", "1abc23", "")),
    ("https://www.reddit.com/r/vcardtrades/", None),
    ("https://example.com/r/x/comments/1abc23/", None),
    ("javascript:alert(1)", None), ("", None), (None, None),
])
def test_which_urls_are_posts(url, expected):
    assert watcher.parse_post_url(url) == expected


def test_a_post_is_linked_to_a_sheet(client, sheet):
    created = client.post(f"/api/trade-sheets/{sheet}/posts", json={"url": POST_URL})
    assert created.status_code == 201
    post = created.get_json()
    assert (post["source"], post["community"], post["status"], post["new"], post["last_checked_at"]) == ("reddit", "vcardtrades", "watching", 0, None)
    assert [item["id"] for item in client.get(f"/api/trade-sheets/{sheet}/posts").get_json()] == [post["id"]]
    assert client.post(f"/api/trade-sheets/{sheet}/posts", json={"url": "https://redd.it/1abc23"}).status_code == 409
    assert client.post(f"/api/trade-sheets/{sheet}/posts", json={"url": "https://example.com/x"}).status_code == 400
    assert client.post("/api/trade-sheets/9999/posts", json={"url": POST_URL}).status_code == 404


def test_posts_and_inbox_belong_to_their_account(client, post, reddit):
    reddit.payload = feed(entry("t1_a", "buyer", "Ember PL8 please", 5))
    client.post(f"/api/sheet-posts/{post}/check")
    admin = login(ADMIN)
    assert admin.post(f"/api/sheet-posts/{post}/check").status_code == 404
    assert admin.patch(f"/api/sheet-posts/{post}", json={"status": "done"}).status_code == 404
    assert admin.delete(f"/api/sheet-posts/{post}").status_code == 404
    assert inbox(admin)["items"] == [] and inbox(admin)["new"] == 0
    item = inbox(client)["items"][0]["id"]
    assert admin.patch(f"/api/inbox/{item}", json={"state": "done"}).status_code == 404
    assert login(None).get("/api/inbox").status_code == 401


# ---- reading the feed ----------------------------------------------------------------------------

def test_new_comments_arrive_in_the_inbox_once(client, post, reddit):
    reddit.payload = feed(entry("t1_a", "buyer", "I'll take the Tide (PL8)", 5), entry("t1_b", "other", "still available?", 7))
    first = client.post(f"/api/sheet-posts/{post}/check").get_json()
    assert first["new"] == 2
    assert (first["post"]["title"], first["post"]["community"], first["post"]["new"], first["post"]["last_error"]) == ("[WTS] Ember and friends", "vcardtrades", 2, "")
    assert reddit.asked == ["https://www.reddit.com/comments/1abc23/.rss?limit=100&sort=new"]

    query("DELETE FROM app_settings WHERE key='reddit_next_request_at'")
    reddit.payload = feed(entry("t1_a", "buyer", "I'll take the Tide (PL8)", 5), entry("t1_b", "other", "still available?", 7), entry("t1_c", "buyer", "pm sent", 9))
    assert client.post(f"/api/sheet-posts/{post}/check").get_json()["new"] == 1

    items = inbox(client)["items"]
    assert [(item["author"], item["body"], item["sheet_name"], item["post_title"]) for item in items] == [
        ("buyer", "pm sent", "Abgabe", "[WTS] Ember and friends"),
        ("other", "still available?", "Abgabe", "[WTS] Ember and friends"),
        ("buyer", "I'll take the Tide (PL8)", "Abgabe", "[WTS] Ember and friends"),
    ]
    assert items[0]["url"].endswith("t1_c/") and inbox(client)["new"] == 3
    assert next(game for game in client.get("/api/bootstrap").get_json()["games"] if game["id"] == "vcard") and client.get("/api/bootstrap").get_json()["inbox_new"] == 3


def test_own_comments_and_the_bot_are_left_out(client, post, reddit):
    client.post("/api/settings", json={"redditUsername": "u/Seller"})
    reddit.payload = feed(entry("t1_a", "AutoModerator", "Please read the rules", 1), entry("t1_b", "seller", "bump", 2), entry("t1_c", "buyer", "interested", 3))
    assert client.post(f"/api/sheet-posts/{post}/check").get_json()["new"] == 1
    assert [item["author"] for item in inbox(client)["items"]] == ["buyer"]


def test_comment_markup_becomes_text():
    markup = '<!-- SC_OFF --><div class="md"><p>Hi &amp; hello<br/>second line</p> <p><a href="https://x">link</a> &lt;3</p></div><!-- SC_ON -->'
    assert watcher.plain_text(markup) == "Hi & hello\nsecond line\n link <3"
    assert watcher.plain_text("") == "" and watcher.plain_text(None) == ""


@pytest.mark.parametrize("body, expected", [
    ("I'll take Ember PL8 and the tide (pl8)", [("Ember (PL8)", True), ("Tide (PL8)", True)]),
    ("is ember still there?", [("Ember (PL8)", False), ("Ember (PL9)", False)]),          # which Ember is not said
    ("Ember #002 for me", [("Ember (PL9)", True)]),                                      # the number settles it
    ("ember 9", [("Ember (PL8)", False), ("Ember (PL9)", False)]),
    ("remember to ship", []),                                                           # not the word "ember"
    ("thanks!", []),
])
def test_which_cards_of_the_sheet_a_comment_names(body, expected):
    cards = [
        {"variant_id": "e8", "canonical_name": "Ember (PL8)", "collector_number": "001", "finish": "Normal"},
        {"variant_id": "e9", "canonical_name": "Ember (PL9)", "collector_number": "002", "finish": "Normal"},
        {"variant_id": "t8", "canonical_name": "Tide (PL8)", "collector_number": "003", "finish": "Normal"},
    ]
    assert [(match["name"], match["certain"]) for match in watcher.cards_mentioned(body, cards)] == expected


def test_matches_are_stored_with_the_comment(client, post, reddit):
    reddit.payload = feed(entry("t1_a", "buyer", "Tide PL8 please", 5))
    client.post(f"/api/sheet-posts/{post}/check")
    assert [(match["variant_id"], match["certain"]) for match in inbox(client)["items"][0]["matches"]] == [(TIDE8, True)]


# ---- being a polite client -----------------------------------------------------------------------

def test_the_source_is_asked_no_more_often_than_it_allows(client, post, reddit):
    reddit.pause = 58
    assert client.post(f"/api/sheet-posts/{post}/check").status_code == 200
    refused = client.post(f"/api/sheet-posts/{post}/check")
    assert refused.status_code == 429 and 50 < refused.get_json()["wait"] <= 60
    assert len(reddit.asked) == 1, "the second request never left"


def test_a_refusal_is_noted_and_backs_off(client, post, reddit):
    reddit.error = watcher.FeedError("Reddit lässt gerade keine weitere Abfrage zu.", 40)
    failed = client.post(f"/api/sheet-posts/{post}/check")
    assert failed.status_code == 502 and "keine weitere Abfrage" in failed.get_json()["post"]["last_error"]
    assert 35 < watcher.wait_seconds(sqlite3.connect(deckledger.config.DB_PATH)) <= 42
    assert inbox(client)["items"] == []


def test_rate_limit_headers_decide_the_pause():
    assert watcher.pause_from({"x-ratelimit-remaining": "0.0", "x-ratelimit-reset": "58"}) == 60
    assert watcher.pause_from({"x-ratelimit-remaining": "42", "x-ratelimit-reset": "300"}) == 2
    assert watcher.pause_from({}) == 2
    assert watcher.pause_from({"x-ratelimit-remaining": "soon"}) == watcher.FALLBACK_PAUSE_SECONDS


def test_an_unreadable_answer_is_an_error_not_a_crash():
    with pytest.raises(watcher.FeedError):
        watcher.parse_feed(b"<html>Too many requests")
    assert watcher.parse_feed(feed()) == ("[WTS] Ember and friends", "seller", "vcardtrades", [])


# ---- the background job --------------------------------------------------------------------------

def run_job():
    connection = sqlite3.connect(deckledger.config.DB_PATH)
    try:
        return watcher.run_due(connection)
    finally:
        connection.close()


def test_each_run_reads_the_post_that_waited_longest(client, sheet, reddit):
    first = client.post(f"/api/trade-sheets/{sheet}/posts", json={"url": "https://redd.it/aaaa1"}).get_json()["id"]
    second = client.post(f"/api/trade-sheets/{sheet}/posts", json={"url": "https://redd.it/bbbb2"}).get_json()["id"]
    reddit.payload = feed(entry("t1_a", "buyer", "hello", 5))
    assert run_job() == {"retired": 0, "checked": first, "new": 1}
    assert "waiting" in run_job(), "the source asked for a pause"
    query("DELETE FROM app_settings WHERE key='reddit_next_request_at'")
    assert run_job()["checked"] == second
    query("DELETE FROM app_settings WHERE key='reddit_next_request_at'")
    assert run_job()["checked"] == first
    assert [url.split("/comments/")[1][:5] for url in reddit.asked] == ["aaaa1", "bbbb2", "aaaa1"]


def test_done_and_quiet_posts_are_not_read(client, post, reddit):
    client.patch(f"/api/sheet-posts/{post}", json={"status": "done"})
    assert run_job() == {"retired": 0, "watching": 0} and reddit.asked == []

    client.patch(f"/api/sheet-posts/{post}", json={"status": "watching"})
    long_ago = (datetime.now(timezone.utc) - timedelta(days=watcher.QUIET_DAYS + 1)).replace(microsecond=0).isoformat()
    query("UPDATE sheet_posts SET created_at=?,last_activity_at=?", (long_ago, long_ago))
    assert run_job() == {"retired": 1, "watching": 0}
    assert query("SELECT status FROM sheet_posts")[0]["status"] == "done"


def test_a_failed_read_does_not_stop_the_job(client, post, reddit):
    reddit.error = watcher.FeedError("Der Post ist nicht (mehr) lesbar (404).")
    summary = run_job()
    assert summary["checked"] == post and "404" in summary["error"]
    assert "404" in query("SELECT last_error FROM sheet_posts")[0]["last_error"]


# ---- the inbox -----------------------------------------------------------------------------------

def test_inbox_entries_can_be_marked_done(client, post, reddit):
    reddit.payload = feed(entry("t1_a", "buyer", "one", 5), entry("t1_b", "buyer", "two", 6), entry("t1_c", "buyer", "three", 7))
    client.post(f"/api/sheet-posts/{post}/check")
    newest = inbox(client)["items"][0]["id"]
    assert client.patch(f"/api/inbox/{newest}", json={"state": "done"}).get_json() == {"saved": True, "new": 2}
    assert len(inbox(client)["items"]) == 2 and len(inbox(client, state="all")["items"]) == 3
    assert client.patch(f"/api/inbox/{newest}", json={"state": "archived"}).status_code == 400
    assert client.post("/api/inbox/done", json={"post_id": post}).get_json()["new"] == 0
    assert inbox(client)["items"] == []


def test_inbox_can_be_narrowed_to_a_game(client, post, reddit):
    reddit.payload = feed(entry("t1_a", "buyer", "one", 5))
    client.post(f"/api/sheet-posts/{post}/check")
    assert len(inbox(client, game_id="vcard")["items"]) == 1 and inbox(client, game_id="lorcana")["items"] == []


def test_removing_a_post_or_its_sheet_clears_its_inbox(client, sheet, post, reddit):
    reddit.payload = feed(entry("t1_a", "buyer", "one", 5))
    client.post(f"/api/sheet-posts/{post}/check")
    assert client.delete(f"/api/sheet-posts/{post}").get_json() == {"deleted": True}
    assert inbox(client, state="all")["items"] == [] and query("SELECT COUNT(*) n FROM sheet_posts")[0]["n"] == 0

    again = client.post(f"/api/trade-sheets/{sheet}/posts", json={"url": POST_URL}).get_json()["id"]
    query("DELETE FROM app_settings WHERE key='reddit_next_request_at'")
    client.post(f"/api/sheet-posts/{again}/check")
    client.delete(f"/api/trade-sheets/{sheet}")
    assert query("SELECT COUNT(*) n FROM sheet_posts")[0]["n"] == 0 and query("SELECT COUNT(*) n FROM inbox_items")[0]["n"] == 0


def test_deleting_an_account_removes_its_posts_and_inbox(client, admin, post, reddit):
    reddit.payload = feed(entry("t1_a", "buyer", "one", 5))
    client.post(f"/api/sheet-posts/{post}/check")
    demo_id = query("SELECT id FROM users WHERE username='demo'")[0]["id"]
    assert admin.delete(f"/api/admin/users/{demo_id}").get_json() == {"deleted": True}
    assert query("SELECT COUNT(*) n FROM sheet_posts")[0]["n"] == 0 and query("SELECT COUNT(*) n FROM inbox_items")[0]["n"] == 0


# ---- one post, several sheets --------------------------------------------------------------------

@pytest.fixture
def four_sheets(client, sheet):
    """The same post linked to four sheets, as when one thread offers several of them."""
    others = []
    for name, variant in (("Zwei", EMBER9), ("Drei", TIDE8), ("Vier", EMBER8)):
        other = client.post("/api/trade-sheets", json={"game_id": "vcard", "name": name}).get_json()["id"]
        client.post(f"/api/trade-sheets/{other}/cards", json={"variant_id": variant, "delta": 1})
        others.append(other)
    sheets = [sheet, *others]
    links = [client.post(f"/api/trade-sheets/{each}/posts", json={"url": POST_URL}).get_json()["id"] for each in sheets]
    return sheets, links


def open_gate():
    query("DELETE FROM app_settings WHERE key='reddit_next_request_at'")


def test_a_post_linked_to_several_sheets_is_read_once_and_files_each_comment_once(client, four_sheets, reddit):
    sheets, links = four_sheets
    reddit.payload = feed(entry("t1_a", "buyer", "Tide PL8 please", 5), entry("t1_b", "other", "still there?", 6))
    assert client.post(f"/api/sheet-posts/{links[2]}/check").get_json()["new"] == 2
    items = inbox(client)["items"]
    assert [(item["author"], item["sheet_id"], item["post_id"]) for item in items] == [("other", sheets[0], links[0]), ("buyer", sheets[0], links[0])]
    assert [match["variant_id"] for match in items[1]["matches"]] == [TIDE8], "the cards of every linked sheet count"
    assert [client.get(f"/api/trade-sheets/{each}/posts").get_json()[0]["new"] for each in sheets] == [2, 2, 2, 2]
    assert len(set(row["last_checked_at"] for row in query("SELECT last_checked_at FROM sheet_posts"))) == 1

    open_gate()
    assert run_job()["new"] == 0
    open_gate()
    assert run_job()["new"] == 0
    assert len(inbox(client)["items"]) == 2 and len(reddit.asked) == 3


def test_own_replies_are_left_out_without_a_name_in_the_settings(client, four_sheets, reddit):
    links = four_sheets[1]
    reddit.payload = feed(entry("t1_a", "buyer", "interested", 5), entry("t1_b", "Seller", "sent you a chat", 6))
    client.post(f"/api/sheet-posts/{links[0]}/check")
    assert [item["author"] for item in inbox(client)["items"]] == ["buyer"]

    query("INSERT INTO inbox_items(user_id,sheet_id,post_id,kind,external_id,author,created_at) SELECT user_id,sheet_id,id,'comment','t1_old','seller','t' FROM sheet_posts WHERE id=?", (links[0],))
    open_gate()
    client.post(f"/api/sheet-posts/{links[0]}/check")
    assert [item["author"] for item in inbox(client, state="all")["items"]] == ["buyer"], "what was filed before is tidied away"


def test_unlinking_one_sheet_keeps_the_comments_of_the_others(client, four_sheets, reddit):
    sheets, links = four_sheets
    reddit.payload = feed(entry("t1_a", "buyer", "hello", 5))
    client.post(f"/api/sheet-posts/{links[0]}/check")
    client.delete(f"/api/sheet-posts/{links[0]}")
    assert [(item["sheet_id"], item["post_id"]) for item in inbox(client)["items"]] == [(sheets[1], links[1])]
    client.delete(f"/api/trade-sheets/{sheets[1]}")
    assert [(item["sheet_id"], item["post_id"]) for item in inbox(client)["items"]] == [(sheets[2], links[2])]
    for link in links[2:]:
        client.delete(f"/api/sheet-posts/{link}")
    assert inbox(client, state="all")["items"] == []


def test_comments_filed_once_per_sheet_are_merged(tmp_path):
    migrations = deckledger.migrations
    connection = sqlite3.connect(tmp_path / "before.db")
    connection.row_factory = sqlite3.Row
    migrations.migrate(connection, migrations=migrations.MIGRATIONS[:7], log=lambda message: None)
    for sheet_id in (4, 5, 6):
        connection.execute("INSERT INTO trade_sheets(id,user_id,game_id,name,created_at,updated_at) VALUES(?,1,'vcard','S','t','t')", (sheet_id,))
        connection.execute("INSERT INTO sheet_posts(id,user_id,sheet_id,source,external_id,url,created_at) VALUES(?,1,?,'reddit','1abc23','u','t')", (sheet_id + 10, sheet_id))
    rows = [(1, 15, "t1_a", "new"), (2, 14, "t1_a", "done"), (3, 16, "t1_a", "new"), (4, 16, "t1_b", "new"), (5, 15, "t1_b", "new")]
    for item_id, link, external_id, state in rows:
        connection.execute("INSERT INTO inbox_items(id,user_id,sheet_id,post_id,kind,external_id,state,created_at) VALUES(?,1,?,?,'comment',?,?,'t')",
                           (item_id, link - 10, link, external_id, state))
    connection.commit()
    assert migrations.migrate(connection, log=lambda message: None) == [8]
    assert [tuple(row) for row in connection.execute("SELECT id,sheet_id,post_id,external_id,state FROM inbox_items ORDER BY id")] == [
        (1, 4, 14, "t1_a", "done"), (4, 4, 14, "t1_b", "new")]
    connection.close()
