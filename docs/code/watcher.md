# Watcher

`ophir_cards/watcher.py`, run by the job script `post_watch.py`, reads public Reddit feeds and files what matters for a user's trade sheets into that user's inbox: new comments on posts the user linked to a sheet, and other people's posts in chosen subreddits that want or offer a sheet's cards (a find, "Fund"). It only reads; nothing is ever posted or answered.

## Public interface

### Data it keeps

- `sheet_posts`: a Reddit post linked to one sheet (`status` `watching` or `done`). At most 12 per sheet (`POSTS_PER_SHEET`); one post may be linked to several sheets and by several users.
- `inbox_items`: `kind` `comment` (with `post_id` = the link it was filed under) or `post` (a find, `post_id` NULL), `state` `new` or `done`, `matches` = JSON list of `{variant_id, name, finish, certain}` (finds also carry `side`). Unique per (sheet, external id).
- `watch_communities`: subreddits a user wants searched, per game (`source` is always `'reddit'`), at most 8 per game. `community_feeds`: when a subreddit was last read and its last error, shared by everyone who lists it.
- Per sheet (edited through `sheets.py`): `trade_sheets.scout` (default 1: the sheet is searched for in other people's posts) and `scout_communities` (comma list of subreddit names, `''` = all of the game's subreddits).
- Per account: `user_settings` key `redditUsername` (written by the generic `POST /api/settings` in `account.py`). Comments and posts by that name are skipped; case and a `u/` or `/u/` prefix are ignored. Without it, the linked post's author counts as the user.
- `app_settings` key `reddit_next_request_at`: the one request gate shared by the job and all users.

### Accepted input

- Post URL: `http(s)://[<sub>.]reddit.com/[r/<name>/]comments/<id>...` or `http(s)://redd.it/<id>`; anything else is a 400.
- Subreddit: `name`, `r/name` or its reddit.com address; 2-21 characters `[A-Za-z0-9_]`, stored lower case.

### Scheduling: `run_due(connection)`

`post_watch.py` opens its own SQLite connection (busy timeout 10 s, foreign keys on) and calls `run_due`. The container starts it every 60 s (`docker-entrypoint.sh`); a non-blocking `flock` on `<data dir>/post-watch.lock` makes an overlapping run exit 0 at once. It prints the summary only when something was new, failed or retired.

Each run:

1. Retires watching links whose last activity (or creation) is older than 30 days (`QUIET_DAYS`) and commits.
2. Stops (`{"waiting": seconds}`) if the gate is closed.
3. Picks one feed: the watching link with the oldest `last_checked_at`, or the subreddit read longest ago among those not read in the last 300 s (`COMMUNITY_INTERVAL_SECONDS`); the subreddit wins when its read is older or equally old.
4. Reads that single feed, commits, and returns a summary (`retired`, then `checked`/`community`, `new` or `error`).

The gate: after a successful request it is closed for 2 s while Reddit's `x-ratelimit-remaining` header is at least 1, otherwise for `x-ratelimit-reset` (or `retry-after`) + 2 s; after a failed request for the answer's wait or 65 s. The effective pace is therefore one feed per job run, about one request a minute. Manual checks go through the same gate; the 300 s minimum for subreddits applies to the job only.

### Reading a post: `check_post(connection, post)`

- Fetches `https://www.reddit.com/comments/<id>/.rss?limit=100&sort=new` (Atom, at most 2 MB, 20 s timeout, own `User-Agent`) once for all links to that post: every user with a watching link, plus the link that was asked for.
- Per user, a comment is filed once (under that user's first-linked sheet for the post), matched against the cards of all that user's sheets linked to the post. Skipped authors: AutoModerator and the user's own name (or the post author); own comments filed earlier are deleted.
- Updates `title`, `community`, `last_checked_at`, `last_error`, and `last_activity_at` (only when something was new).
- Returns the number of new entries for the owner of `post`. Raises `FeedError` after storing the error on the links and closing the gate. The caller commits.

### Searching a subreddit: `check_community(connection, source, name)`

- Fetches `https://www.reddit.com/r/<name>/new/.rss?limit=100`. Nothing is written before the feed has been read.
- `file_finds`: for every user who lists the subreddit, each of their sheets of that game with `scout=1` whose `scout_communities` is empty or names it. Skipped: posts older than 7 days (`FIND_DAYS`; an unreadable date is kept), AutoModerator, posts the user has linked to a sheet, posts by the user's Reddit name. A post that fits is filed once per sheet.
- Returns the number of new entries; raises `FeedError` after storing the error on `community_feeds`. The caller commits.

### Card matching

- `cards_mentioned(text, cards)`: text lower-cased, everything but letters and digits turned into spaces, whole-word matches only. The full card name is a certain match. The name without its bracketed part ("Ember" of "Ember (PL8)", at least 3 characters) is a match, certain only when the collector number (leading zeros ignored) also appears. When one card of a base name matched certainly, the uncertain matches of the same base name are dropped.
- `post_sides(title, body)`: `[H]`/`[W]` markers (also `have`, `haves`, `want`, `wants`) split the title into "has" and "wants" parts, and the body only "mentions". Without markers the title's tag decides for title and body together: WTS, FS, for sale, selling, `[S]` -> has; WTB, LF, ISO, looking for, buying, `[B]` -> wants; both or neither -> mentions.
- `cards_found(post, cards, wanted)`: for a wanted sheet (WTB, WTTF, WTB/WTTF) the needed side is "has", for any other sheet "wants"; mentions count too. A card found on the other side is no find unless it also stands on the needed side.

### Python functions used by other modules

- `run_due(connection)`: `post_watch.py`. Takes a plain `sqlite3` connection, sets `row_factory` itself, commits.
- `hand_over_comments(connection, link_ids)`: `sheets.py`, before a sheet is deleted. Comments filed under those links move to another link of the same user to the same post, if there is one. Does not commit.

### HTTP endpoints

All are `@login_required` and scoped to the session user.

| Method | Path | What it does |
| --- | --- | --- |
| GET / POST | `/api/trade-sheets/<sheet_id>/posts` | List the sheet's linked posts (each with its count of new inbox items); link one by `{url}` (400 bad URL or limit, 409 already linked). |
| PATCH / DELETE | `/api/sheet-posts/<post_id>` | `{status: watching|done}` (watching again restarts the 30 quiet days); delete the link, handing its comments over first, else deleting them. |
| POST | `/api/sheet-posts/<post_id>/check` | Read the post now. 429 with `wait` while the gate is closed, 502 with the error when Reddit fails. |
| GET / POST | `/api/communities` | List subreddits (optional `game_id`) with last read and error; add `{game_id, name}` (404 game, 400 name or limit, 409 duplicate). |
| DELETE | `/api/communities/<id>` | Remove from the list; its finds stay in the inbox; the shared feed row goes when nobody lists it any more. |
| POST | `/api/communities/<id>/check` | Read the subreddit now; same 429/502 rules as for posts. |
| GET | `/api/inbox` | Up to 200 entries, newest first, with sheet name/kind and post URL/title. Default only `new`; `state=all` includes done ones; optional `game_id`. Also returns the total `new` count. |
| PATCH | `/api/inbox/<id>` | `{state: new|done}`. |
| POST | `/api/inbox/done` | Mark everything new as done, or only that of one post (`post_id`, all the user's links to the same post). |

## Dependencies

- Reddit public Atom feeds on `www.reddit.com` - comments of linked posts and new posts of subreddits; no account, no API key.
- `ophir_cards.web` - Flask app, `db()`, `login_required`, `user_id`.
- `ophir_cards.sheets` - `WANTED_SHEET_KINDS`, `own_sheet`.
- `ophir_cards.config` - `jload`, `now_iso`, `DB_PATH` (`post_watch.py`).
- `fcntl` - lock in `post_watch.py`, so the script runs on POSIX only.
- SQLite tables `sheet_posts`, `inbox_items`, `watch_communities`, `community_feeds` (migrations `m0005`, `m0006`, `m0008`), `trade_sheets`, `trade_sheet_cards`, `user_settings`, `app_settings`, catalogue tables for card names.

## Decisions and assumptions

- Reddit allows an anonymous client about one request a minute (per public IP, README) and says so in its headers; every request, the job's and a user's "check now", therefore goes through one gate, and each run reads a single feed, the one that has waited longest (module docstring, commit 6a7490a).
- A post linked to several sheets is read once, and a comment lands in a user's inbox once, under the sheet linked first; unlinking hands the comments to the next link (commit 1e62a08, m0008).
- Without a Reddit name, the post's author counts as the user, so one's own replies stay out of the inbox (commit 1e62a08).
- No write before the feed has been read, so the database's write lock is not held while waiting on Reddit (`check_community` docstring, commit 3904924).
- Posts nobody commented on for 30 days stop being read; finds older than 7 days are dropped because their author has most likely found a partner (constants' comments).
- The `source` columns leave room for other sources, but URLs, feeds and names are Reddit-only today.
