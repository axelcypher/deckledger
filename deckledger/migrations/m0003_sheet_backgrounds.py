"""Backgrounds for trade sheets that a user uploaded. The image itself is a file in the data
directory (sheet-backgrounds/<id>.jpg); a sheet refers to one as "custom-<id>"."""

NAME = "uploaded sheet backgrounds"


def apply(connection, context):
    connection.execute("""CREATE TABLE sheet_backgrounds (
      id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id),
      name TEXT NOT NULL, accent TEXT NOT NULL, created_at TEXT NOT NULL
    )""")
    connection.execute("CREATE INDEX idx_sheet_backgrounds_user ON sheet_backgrounds(user_id)")
