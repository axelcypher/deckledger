"""A comment is in a user's inbox once, however many of their sheets are linked to its post.

Until now every link filed every comment, so a post linked to four sheets showed each comment
four times. Of those copies the oldest stays -- dealt with if any copy was -- and moves to the
link that was made first; the watcher files new comments there from now on.
"""

NAME = "one inbox entry per comment"


def apply(connection, context):
    context.run_script("""
      CREATE TEMP TABLE kept AS
        SELECT MIN(id) id, MAX(state='done') done, MIN(post_id) a_link, NULL first_link
        FROM inbox_items WHERE kind='comment' GROUP BY user_id, external_id;
      UPDATE kept SET first_link=(SELECT MIN(o.id) FROM sheet_posts p JOIN sheet_posts o
        ON o.user_id=p.user_id AND o.source=p.source AND o.external_id=p.external_id WHERE p.id=kept.a_link);
      DELETE FROM inbox_items WHERE kind='comment' AND id NOT IN (SELECT id FROM kept);
      UPDATE inbox_items SET
        state=CASE WHEN (SELECT done FROM kept WHERE kept.id=inbox_items.id) THEN 'done' ELSE state END,
        post_id=(SELECT first_link FROM kept WHERE kept.id=inbox_items.id),
        sheet_id=(SELECT s.sheet_id FROM kept JOIN sheet_posts s ON s.id=kept.first_link WHERE kept.id=inbox_items.id)
      WHERE id IN (SELECT id FROM kept);
      DROP TABLE kept;
    """)
