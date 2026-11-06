"""The link of an account to the master's account when the app takes its sign-in from another
Ophir app (OPHIR_AUTH_MODE=slave, ophir_cards/shared_auth.py).

A column of its own rather than oauth_provider/oauth_subject: an account keeps its SSO link when
the app goes back to signing people in itself.
"""

NAME = "shared sign-in subject"


def apply(connection, context):
    context.run_script("""
ALTER TABLE users ADD COLUMN auth_subject TEXT NOT NULL DEFAULT '';
CREATE UNIQUE INDEX idx_users_auth_subject ON users(auth_subject) WHERE auth_subject != '';
""")
