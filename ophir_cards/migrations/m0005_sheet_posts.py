"""Posts a sheet was published as, and the inbox of what happens there.

sheet_posts: one row per post (so far: Reddit) a user linked to one of their sheets. The watcher
reads its comments while status is 'watching'.
inbox_items: what the watcher found for a user -- so far comments on those posts. external_id is
the source's own id of the item, which is what keeps a comment from being reported twice.
"""

NAME = "sheet posts and inbox"


def apply(connection, context):
    connection.execute("""CREATE TABLE sheet_posts (
      id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id),
      sheet_id INTEGER NOT NULL REFERENCES trade_sheets(id) ON DELETE CASCADE,
      source TEXT NOT NULL, external_id TEXT NOT NULL, url TEXT NOT NULL, community TEXT NOT NULL DEFAULT '',
      title TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'watching',
      created_at TEXT NOT NULL, last_checked_at TEXT, last_activity_at TEXT, last_error TEXT NOT NULL DEFAULT '',
      UNIQUE(sheet_id, source, external_id)
    )""")
    connection.execute("CREATE INDEX idx_sheet_posts_due ON sheet_posts(status, last_checked_at)")
    connection.execute("""CREATE TABLE inbox_items (
      id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id),
      post_id INTEGER NOT NULL REFERENCES sheet_posts(id) ON DELETE CASCADE,
      kind TEXT NOT NULL, external_id TEXT NOT NULL, author TEXT NOT NULL DEFAULT '', body TEXT NOT NULL DEFAULT '',
      url TEXT NOT NULL DEFAULT '', posted_at TEXT NOT NULL DEFAULT '', matches TEXT NOT NULL DEFAULT '[]',
      state TEXT NOT NULL DEFAULT 'new', created_at TEXT NOT NULL,
      UNIQUE(post_id, external_id)
    )""")
    connection.execute("CREATE INDEX idx_inbox_user_state ON inbox_items(user_id, state)")
