"""The demo account is gone: new databases no longer get it, and one that still has its published
password ("demo" / "deckledger") can no longer sign in -- on an instance reachable from outside
anyone could. Its collection and lists stay; an admin deletes the account under Admin -> Benutzer
or gives it a new password.
"""

from werkzeug.security import check_password_hash

NAME = "lock the demo account"


def apply(connection, context):
    row = connection.execute("SELECT id, password_hash FROM users WHERE username='demo'").fetchone()
    if row and row[1] and check_password_hash(row[1], "deckledger"):
        connection.execute("UPDATE users SET password_hash='' WHERE id=?", (row[0],))
