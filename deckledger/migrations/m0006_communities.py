"""Communities whose new posts are searched for a sheet's cards, and what was found there.

watch_communities: the communities (so far: subreddits) a user wants searched, per game.
community_feeds: when a community was last read -- once for everybody who has it in their list.
trade_sheets.scout / scout_communities: whether a sheet is looked for in other people's posts,
and in which of the game's communities ('' = all of them).
inbox_items now also holds posts found that way (kind 'post'). Those have no linked post, so an
entry belongs to its sheet directly; sheet_id + external_id is what keeps it from being reported
twice. The table is rebuilt because post_id has to allow NULL.
"""

NAME = "communities to search, finds in the inbox"


def apply(connection, context):
    connection.execute("""CREATE TABLE watch_communities (
      id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id),
      game_id TEXT NOT NULL REFERENCES games(id), source TEXT NOT NULL, name TEXT NOT NULL, created_at TEXT NOT NULL,
      UNIQUE(user_id, game_id, source, name)
    )""")
    connection.execute("""CREATE TABLE community_feeds (
      source TEXT NOT NULL, name TEXT NOT NULL, last_checked_at TEXT, last_error TEXT NOT NULL DEFAULT '',
      PRIMARY KEY(source, name)
    )""")
    connection.execute("ALTER TABLE trade_sheets ADD COLUMN scout INTEGER NOT NULL DEFAULT 1")
    connection.execute("ALTER TABLE trade_sheets ADD COLUMN scout_communities TEXT NOT NULL DEFAULT ''")
    connection.execute("ALTER TABLE inbox_items RENAME TO inbox_items_before_0006")
    connection.execute("""CREATE TABLE inbox_items (
      id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id),
      sheet_id INTEGER NOT NULL REFERENCES trade_sheets(id) ON DELETE CASCADE,
      post_id INTEGER REFERENCES sheet_posts(id) ON DELETE CASCADE,
      kind TEXT NOT NULL, external_id TEXT NOT NULL, author TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '',
      body TEXT NOT NULL DEFAULT '', url TEXT NOT NULL DEFAULT '', community TEXT NOT NULL DEFAULT '',
      posted_at TEXT NOT NULL DEFAULT '', matches TEXT NOT NULL DEFAULT '[]',
      state TEXT NOT NULL DEFAULT 'new', created_at TEXT NOT NULL,
      UNIQUE(sheet_id, external_id)
    )""")
    connection.execute("""INSERT INTO inbox_items(id,user_id,sheet_id,post_id,kind,external_id,author,body,url,community,posted_at,matches,state,created_at)
      SELECT i.id,i.user_id,p.sheet_id,i.post_id,i.kind,i.external_id,i.author,i.body,i.url,p.community,i.posted_at,i.matches,i.state,i.created_at
      FROM inbox_items_before_0006 i JOIN sheet_posts p ON p.id=i.post_id""")
    connection.execute("DROP TABLE inbox_items_before_0006")
    connection.execute("CREATE INDEX idx_inbox_user_state ON inbox_items(user_id, state)")
