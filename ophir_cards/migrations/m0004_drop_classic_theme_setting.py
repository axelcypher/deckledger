"""The "Klassisch" theme is gone; the setting that chose between it and the current one with it."""

NAME = "drop the classic theme setting"


def apply(connection, context):
    connection.execute("DELETE FROM user_settings WHERE key='mobileTheme'")
