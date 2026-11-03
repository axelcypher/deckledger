# Authentication, accounts and admin

`deckledger/auth.py` signs users in with a local password or the one optional OAuth2/OIDC provider, whose protocol mechanics live in `oauth_client.py` (repository root). `deckledger/account.py` holds a user's own settings, account data and password, and `deckledger/admin.py` the admin API for accounts, games, catalogue providers, manual cards and SSO settings.

## Public interface

### deckledger/auth.py

| Method | Path | Auth | What it does |
| --- | --- | --- | --- |
| GET | `/login` | none | Login page (`login.html`); a signed-in user is redirected to `/`. `?error=<code>` shows the message for that code. |
| POST | `/login` | none | Form fields `username`, `password`. Success: session cleared, `session["user_id"]` set, redirect to `/`. Failure: the page again with 401. An account with an empty `password_hash` cannot sign in locally. |
| GET | `/oauth/login` | none | Starts the authorization code flow with PKCE and redirects to the provider. Stores `oauth_state`, `oauth_code_verifier` and `oauth_link_user_id` in the session; with a signed-in session the round trip is a link flow. |
| GET | `/oauth/callback` | none | Checks `state`, exchanges the code, reads the identity, then links it (link flow) or signs in (login flow). |
| POST | `/logout` | none | Clears the session, redirects to `/login`. |

- Error codes (`OAUTH_ERROR_MESSAGES`): `not_configured`, `state_mismatch`, `provider_error`, `no_account`, `already_linked`. A login-flow failure redirects to `/login?error=<code>`; a link-flow failure to `/?oauth_error=<code>` (shown as a toast by static/js/main.js); a successful link to `/?linked=1`.
- Link flow: refused with `already_linked` when another account has the identity; otherwise sets `oauth_provider`/`oauth_subject` on the signed-in account and its email only if it had none.
- `resolve_oauth_config()` - `OAUTH_CONFIG_DEFAULTS` overlaid by the JSON file at `OAUTH_CONFIG_PATH` when it exists (`source: "file"`; an unreadable or invalid file gives the defaults), else by `app_settings` key `oauth_config` (`source: "database"`). Read fresh on every call. Without a file it needs an app context (uses `db()`). Also used by `pages.py` (bootstrap) and `admin.py`.
- `resolve_oauth_identity(config, subject, email, display_name, email_verified=True)` - for the login flow; returns a `users` row or None and commits what it changes. Chain, each step only if the previous found nothing:
  1. the account linked to (`generic`, subject);
  2. with `account_matching` `email` or `auto_provision` and an email: the unlinked account with that email (case-insensitive) gets linked - unless the email is unverified, then None;
  3. with `auto_provision`: a new account (role `user`, empty password hash, username from the email's local part, else the display name, else the subject, made unique with `-2`, `-3`, ...) plus its default lists.
- `unique_username_from(candidate)` - slug of the candidate with a numeric suffix until free.

### oauth_client.py (repository root)

No Flask or database coupling; every function takes the resolved config dict. Functions that talk to the provider raise only `OAuthConfigError` (any other exception is wrapped).

- `build_authorization_request(config, redirect_uri) -> (auth_url, state, code_verifier)` - PKCE S256. The caller keeps `state` and `code_verifier` (in the session) and passes them back; the module holds no flow state.
- `exchange_code(config, redirect_uri, code, code_verifier)` - token dict.
- `fetch_userinfo(config, token)` - claims; a non-2xx answer raises with the `WWW-Authenticate` header or the start of the body.
- `id_token_claims(config, token)` - claims of the ID token or `{}`. The signature is not checked; `aud` must contain `client_id` and `exp` must be in the future.
- `extract_identity(config, claims) -> (subject, email, display_name)` - by the configured claim names; raises when the subject is missing. Display name falls back from the username claim to `name`, email, subject.
- `resolved_endpoints(config)` - manually set endpoints win, discovery fills the missing ones; raises when an endpoint, the client id or the client secret is missing. `discover_endpoints(url)` caches per discovery URL for 300 s.
- Every request: 10 s timeout, User-Agent `DeckLedger (OAuth client)`.

### deckledger/account.py

| Method | Path | Auth | What it does |
| --- | --- | --- | --- |
| POST | `/api/settings` | `login_required` | JSON object; each key is upserted into `user_settings` with its value JSON-encoded. Any key is accepted except `publicProfile`, which is skipped. Non-object: 400. |
| PATCH | `/api/account` | `login_required` | `display_name` (not empty), `username` (3-32 of `a-zA-Z0-9._-`, unique ignoring case, else 409), `email` (simple pattern, unique ignoring case among non-empty ones, else 409; empty clears it). Returns `id, username, display_name, email, role`; nothing to change: 400. |
| POST | `/api/account/password` | `login_required` | `new_password` at least 8 characters. `current_password` must match, unless the stored hash is empty (an SSO-only account sets its first password). Wrong current password: 401. |
| POST | `/api/account/oauth/unlink` | `login_required` | Clears the SSO link; refused (400) while the account has no local password. |

### deckledger/admin.py

Every route is `@admin_required` (401 without a session, 403 for a non-admin on `/api/...`).

| Method | Path | What it does |
| --- | --- | --- |
| GET | `/api/admin/users` | All accounts with `oauth_linked`, `password_set`, copies, deck count, `is_self`. |
| POST | `/api/admin/users` | Create: `username` and `password` required; `display_name` (default username), `email`, `role` (`user` default, or `admin`). Creates the default lists. 201 `{id}`. |
| PATCH | `/api/admin/users/<id>` | `username`, `display_name` (blank ignored), `email`, `role`, `password` (new hash), `unlink_oauth`. |
| DELETE | `/api/admin/users/<id>` | Deletes the account and everything it entered, including its sheet-background and eBay-photo files. |
| GET | `/api/admin/games` | All `games` rows (`languages`, `rarity_order` parsed). |
| POST | `/api/admin/games` | Create a module-less game: `name` required, `id` (slug of it or of the name), `short_name`, `languages` (default `["EN"]`), `accent`. 409 if the id exists. |
| PATCH | `/api/admin/games/<id>` | `name`, `short_name`, `accent`, `price_method`, `deck_ruleset`, `cardmarket_game_id`, `languages`, `rarity_order`, `enabled`. |
| DELETE | `/api/admin/games/<id>` | Deletes the game's catalogue, prices, user data of that game, providers, price overrides, import operations and uploaded card back. |
| POST | `/api/admin/games/<id>/card-back` | Multipart `file`, saved as `card-backs/<id>.jpg`. |
| GET | `/api/admin/providers` | All `catalog_providers` rows (`last_summary` parsed). |
| POST | `/api/admin/providers` | New provider with custom code (`game_id`, `code` required; `id`, `label`, `minimum_sets`, `minimum_cards`, `timeout_seconds` default 300); `customized=1`. |
| PATCH | `/api/admin/providers/<id>` | `code` (new `provider_version`), `label`, minimums, `timeout_seconds`, `enabled`. |
| DELETE | `/api/admin/providers/<id>` | Deletes the row. |
| POST | `/api/admin/providers/<id>/run` | Runs `python catalog_sync.py --provider <id>` synchronously inside the request; 504 after `timeout_seconds + 30`. Returns `returncode`, `status`, `summary`, `error`, `run_at` and, on failure, the last 2000 characters of stdout and stderr. |
| GET | `/api/admin/games/<id>/manual-cards` | Manually created cards with printings and variants. |
| POST | `/api/admin/games/<id>/manual-cards` | Create or update a manual card (identity, printing, variant; optionally a new set). 201 with the three ids. |
| DELETE | `/api/admin/games/<id>/manual-cards/<identity_id>` | Deletes a manual card and every collection, deck, list, sheet, price and eBay draft/tracked reference to it. |
| GET | `/api/admin/oauth` | Resolved SSO config without `client_secret`, plus `client_secret_set`, `config_path`, `source`. |
| POST | `/api/admin/oauth` | Saves SSO settings into `app_settings.oauth_config`. |

Invariants and error modes:

- The last admin can be neither demoted nor deleted; nobody deletes their own account here (400).
- User fields are validated by `user_fields_error` (same username/email rules as account.py, password at least 8, role `user` or `admin`) -> 400 with a German message.
- Account deletion removes child rows before parents because foreign keys are on; catalogue and prices are shared and stay.
- Provider `code` edits set `customized = 0` exactly when the code equals the shipped `providers/` file, so a reverted provider follows shipped updates again (see schema.py seed).
- Manual cards get `source_type = 'manual-override'` and ids built from slugs: set `<game>-<slug of the code>`, identity `<game>-card-<key>`, printing `<game>-print-<slug of the set id>-<key>-<language, lower case>`, variant `<printing>-<slug of the finish, or normal>` (`key` = slug of `key` or of the name). Rows that belong to imported data are never overwritten (409); only manual cards can be deleted (400 otherwise).
- SSO save: refused (400) while a config file is mounted; `account_matching` must be `manual`, `email` or `auto_provision`; `enabled` is read from every payload (absent = off); a blank `client_secret` keeps the stored one.

## Dependencies

- `deckledger.web` - `app`, `db`, `login_required`, `admin_required`, `user_id`
- `deckledger.config` - `OAUTH_CONFIG_DEFAULTS`, `OAUTH_CONFIG_PATH`, `OAUTH_PROVIDER_KEY`, `CARD_BACK_UPLOAD_DIR`, `ROOT`, `jload`, `now_iso`
- `deckledger.schema` - `ensure_user_lists`, `default_provider_code`
- `deckledger.games` - `GAMES` (is a provider shipped)
- `deckledger.sheets`, `deckledger.ebay_photos` - file clean-up when an account is deleted
- `catalog_provider_contract` - `slug`, `digest`
- `catalog_sync.py` - started as a subprocess by the provider run endpoint
- Authlib (`OAuth2Session`, `generate_token`) and requests - used by `oauth_client.py`
- werkzeug.security - password hashes
- External: the configured identity provider (discovery document, authorize, token and userinfo endpoints)

## Decisions and assumptions

- OAuth mechanics are delegated to Authlib instead of hand-rolled PKCE/state/token code (oauth_client.py docstring).
- Exactly one provider; `users.oauth_provider` always holds `generic`, and `(oauth_provider, oauth_subject)` is unique for linked rows (index from migration 0001).
- A mounted config file always wins and makes the Admin UI read-only; without it the settings are read from the database on every call, so a save applies at the next login click without a restart.
- Link versus login is decided by whether the browser already had a session when `/oauth/login` was opened. Link failures go to `/` because `/login` immediately redirects a signed-in user and would swallow the error.
- The ID token's claims are trusted without a signature check because it came straight from the token endpoint in answer to a request authenticated with the client secret (OIDC Core 3.1.3.7). The userinfo request may fail as long as the ID token names the subject (Authentik answered userinfo with 403; commit 91e700e).
- Matching by email includes admin accounts (commit 0992871). An address the provider reports as unverified is refused when it matches an existing account; a missing `email_verified` claim counts as verified.
- `OAUTH_CONFIG_DEFAULTS` sets `account_matching` to `auto_provision`; the `manual` fallback in `resolve_oauth_identity` only applies when the value is empty.
- Both sign-in paths clear the session before setting `user_id`.
- There are no CSRF tokens: the login form, `/logout` and all API writes rely on `web.reject_cross_site_writes`.
- Username uniqueness is checked ignoring case, but `/login` looks the username up exactly as typed.
- The demo account is gone; migration 0010 locks one that still has its published password (commit 08cfde7).
