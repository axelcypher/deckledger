"""Serves the app for the browser tests (tests/frontend/*.test.mjs): the working tree, a throwaway
database and the same small catalogue the Python tests use.

    python tests/frontend/serve.py <port>
"""
import sys
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
conftest.deckledger.watcher.fetch_feed = lambda url: (COMMENT_FEED, 2)
conftest.deckledger.app.run(host="127.0.0.1", port=int(sys.argv[1]), debug=False, threaded=True)
