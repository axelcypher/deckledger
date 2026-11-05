"""Serves the app for the browser tests (tests/frontend/*.test.mjs): the working tree, a throwaway
database and the same small catalogue the Python tests use.

    python tests/frontend/serve.py <port> [<base path>]

With a base path (e.g. /cards) the app is served under it, the way a reverse proxy serves it as
ophir.<domain>/cards; everything outside it answers 404.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import conftest  # noqa: E402  -- points DATABASE_PATH at a temp file before the app is imported

conftest.catalog_sync.write_database(conftest.sample_catalog(), {"vcard", "lorcana", "one-piece"})

# The browser tests must not reach Reddit: every linked post has this one comment.
COMMENT_FEED = b"""<?xml version="1.0" encoding="UTF-8"?><feed xmlns="http://www.w3.org/2005/Atom">
  <category term="vcardtrades" label="r/vcardtrades"/>
  <entry><author><name>/u/demo_seller</name></author><content type="html">mine</content><id>t3_1abc23</id>
    <link href="https://www.reddit.com/r/vcardtrades/comments/1abc23/wts/"/><updated>2026-10-02T10:00:00+00:00</updated><title>[WTS] Test post</title></entry>
  <entry><author><name>/u/buyer_one</name></author><content type="html">&lt;div class="md"&gt;&lt;p&gt;I would take the Ember PL8 &lt;b&gt;today&lt;/b&gt;&lt;/p&gt;&lt;/div&gt;</content><id>t1_c1</id>
    <link href="https://www.reddit.com/r/vcardtrades/comments/1abc23/wts/c1/"/><updated>2026-10-02T10:05:00+00:00</updated><title>comment</title></entry>
</feed>"""

# ... and every community has these two new posts: one that wants the card, one that has it.
def new_posts():
    posted = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    entry = """<entry><author><name>/u/{author}</name></author><content type="html">&lt;p&gt;see &lt;b&gt;pictures&lt;/b&gt;&lt;/p&gt;</content><id>t3_{id}</id>
      <link href="https://www.reddit.com/r/vcardtrades/comments/{id}/post/"/><updated>{posted}</updated><published>{posted}</published><title>{title}</title></entry>"""
    return ("""<?xml version="1.0" encoding="UTF-8"?><feed xmlns="http://www.w3.org/2005/Atom"><category term="vcardtrades" label="r/vcardtrades"/>"""
            + entry.format(author="buyer_two", id="want1", posted=posted, title="[US] [H] PayPal [W] Ember PL8")
            + entry.format(author="seller_two", id="have1", posted=posted, title="[US] [H] Ember PL8 [W] PayPal") + "</feed>").encode()


conftest.ophir_cards.watcher.fetch_feed = lambda url: (new_posts() if "/r/" in url else COMMENT_FEED, 2)

# Nor the publishers' sites: every game's wordmark is already in the logo cache (a 1x1 PNG), so a
# publisher moving its logo does not fail the browser tests.
PIXEL = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                      "1f15c4890000000d49444154789c6360f8cfc0f01f0005000201e5270ade0000000049454e44ae426082")
conftest.ophir_cards.config.IMAGE_CACHE.mkdir(parents=True, exist_ok=True)
for game_id in conftest.ophir_cards.games.GAMES:
    (conftest.ophir_cards.config.IMAGE_CACHE / f"brand-{game_id}.img").write_bytes(PIXEL)
    (conftest.ophir_cards.config.IMAGE_CACHE / f"brand-{game_id}.mime").write_text("image/png")

base_path = sys.argv[2] if len(sys.argv) > 2 else ""
if base_path:
    from werkzeug.exceptions import NotFound
    from werkzeug.middleware.dispatcher import DispatcherMiddleware
    from werkzeug.serving import run_simple

    # SCRIPT_NAME carries the path, as ProxyFix makes it from X-Forwarded-Prefix behind Traefik.
    run_simple("127.0.0.1", int(sys.argv[1]), DispatcherMiddleware(NotFound(), {base_path: conftest.ophir_cards.app}), threaded=True)
else:
    conftest.ophir_cards.app.run(host="127.0.0.1", port=int(sys.argv[1]), debug=False, threaded=True)
