"""The fixed "Verkaufsliste" among the watchlists becomes a trade sheet; its columns go.

That list was the forerunner of the trade sheets (named_watchlists.is_sale_list=1; its entries
were tagged source='auto' when the app had put them there for stock beyond a playset, 'manual'
otherwise). Every such list that holds cards becomes a WTS sheet of the same name with the same
cards and quantities, then the list is removed. An 'auto' entry is only taken over while its card
is still surplus -- the list dropped those the next time it was opened.

List names are unique per account and game, so where an account already had a list of its own
called "Verkaufsliste" the fixed one was never created next to it and that list served as the sale
list. It is taken over the same way.

The release before numbered migrations already did this conversion at start-up and noted it in
app_settings ('sale_lists_migrated'). On such a database only lists still flagged are converted --
a watchlist that was given that name afterwards stays a watchlist -- and the note is removed.
"""

NAME = "sale lists become trade sheets"

SALE_LIST_NAME = "Verkaufsliste"
# Copies that make a playset, as they were when this migration was written.
PLAYSET_SIZES = {"vcard": 3}


def apply(connection, context):
    stamp = context.now
    by_name = not connection.execute("SELECT 1 FROM app_settings WHERE key='sale_lists_migrated'").fetchone()
    for list_id, uid, game_id, name, created_at in connection.execute(
        "SELECT id,user_id,game_id,name,created_at FROM named_watchlists WHERE is_sale_list=1 OR (? AND is_default=0 AND name=?) ORDER BY id",
        (by_name, SALE_LIST_NAME),
    ).fetchall():
        entries = connection.execute(
            """SELECT e.variant_id,e.quantity FROM named_watchlist_entries e WHERE e.list_id=? AND (e.source!='auto' OR
                 (SELECT COALESCE(SUM(c.quantity),0) FROM collection_entries c WHERE c.user_id=? AND c.variant_id=e.variant_id)>?)
               ORDER BY e.id""", (list_id, uid, PLAYSET_SIZES.get(game_id, 4)),
        ).fetchall()
        if entries:
            sheet_id = connection.execute(
                "INSERT INTO trade_sheets(user_id,game_id,name,kind,created_at,updated_at) VALUES(?,?,?,'WTS',?,?)",
                (uid, game_id, name, created_at or stamp, stamp),
            ).lastrowid
            connection.executemany(
                "INSERT OR IGNORE INTO trade_sheet_cards(sheet_id,variant_id,quantity) VALUES(?,?,?)",
                [(sheet_id, variant_id, max(1, min(99, quantity))) for variant_id, quantity in entries],
            )
        connection.execute("DELETE FROM named_watchlist_entries WHERE list_id=?", (list_id,))
        connection.execute("DELETE FROM named_watchlists WHERE id=?", (list_id,))
    connection.execute("DELETE FROM app_settings WHERE key='sale_lists_migrated'")
    connection.execute("ALTER TABLE named_watchlists DROP COLUMN is_sale_list")
    connection.execute("ALTER TABLE named_watchlist_entries DROP COLUMN source")
