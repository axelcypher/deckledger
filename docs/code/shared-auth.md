# Shared sign-in (Ophir apps)

The apps of the Ophir series can run side by side on one host, each under a path of its own (`ophir.<domain>/cards`, `/figures`). One of them is the **master**: people sign in there once, under `/auth` on that host, and every other app (**slave**) takes that sign-in. Signing out anywhere signs out everywhere.

The protocol is in `ophir_auth.py` (repository root). The same file is in every Ophir app; change it in all of them at once and bump `VERSION` when the token changes. This document is the reference for both sides; Ophir Cards' integration is in `ophir_cards/shared_auth.py`.

## Settings

| Variable | Values | Meaning |
| --- | --- | --- |
| `OPHIR_AUTH_MODE` | `standalone` (default), `master`, `slave` | Whether the app signs its users in itself, also for the others, or takes the master's sign-in. Anything else stops the app at start-up. |
| `OPHIR_AUTH_PATH` | default `/auth` | The path on the shared host where the master serves sign-in. Master and slaves must agree. |
| `OPHIR_AUTH_HOME` | default `/` | Master only: where a sign-in at the auth path ends when it did not say where to go (e.g. `/cards/`). |

Every app also needs the same `SECRET_KEY` (in the homelab: `kv/docker-secrets/ophir`). It signs the shared token. Each app keeps its own session cookie (own name, own salt, limited to its path), so apps on one host never read each other's sessions; only the shared token crosses apps.

## Routing (reverse proxy)

- The master is served under its own path (`/cards`) **and** under the auth path (`/auth`), both with the path stripped and passed on as `X-Forwarded-Prefix` (Traefik `stripPrefix`); `TRUST_PROXY_HEADERS` on.
- Under the auth path the master serves only `login`, `logout`, `oauth/login`, `oauth/callback`, its static files and `health`; everything else answers 404 there.
- Slaves are served under their own path only. They need no route of their own for sign-in.
- One SSO redirect URI for all apps: `https://<host><auth path>/oauth/callback`.

## The token

- Cookie `ophir_auth`, path `/` (the whole host), `HttpOnly`, `SameSite=Lax`, `Secure` over https, no expiry date (a browser-session cookie, like the apps' own sessions).
- Value: `itsdangerous.URLSafeTimedSerializer(SECRET_KEY, salt="ophir-auth-v1").dumps({"v": 1, "sub", "username", "name", "email", "role"})`. `sub` is the master's account id; `role` is `admin` or `user`. All values are strings.
- A slave accepts a token for `FRESH_SECONDS` (12 h) after it was signed. The master renews it on its own pages once it is older than `RENEW_AFTER_SECONDS` (1 h) and on every visit of `<auth path>/login`, each time after checking that the account still exists; it renews tokens up to `MASTER_MAX_AGE_SECONDS` (30 days) old. An account deleted or demoted at the master therefore stops working at the slaves within 12 hours.
- `safe_next(value, default)` validates every `next`: only paths on the same host (`/…`, not `//…`, no backslashes or control characters).

## Master

- Identity of every request comes from the token: no valid token means not signed in, also for the master's own pages. A sign-in (password or SSO) sets the session's account as usual; the response then carries a new token. A sign-out clears the session, and the response clears the token.
- The token is only renewed or cleared after the request's session was set from the token (`g.shared_synced`). A request stopped before that, by another hook (a refused cross-site write) or the 404 under the auth path, leaves the cookie alone: its session cookie may still name someone who signed out elsewhere, and handing that out again would undo the sign-out.
- `<auth path>/login?next=<path>`: the sign-in form (password, and SSO when configured). Someone who is already signed in (valid token, account exists) is sent to `next` at once with a renewed token. This is how a slave's outdated token gets renewed without a form.
- `<auth path>/oauth/login?next=<path>` starts SSO; the callback ends at `next`. A signed-in person linking SSO to their account (Settings) uses the same route and comes back with `?linked=1` or `?oauth_error=<code>`.
- `<auth path>/logout` (POST) signs out and sends people to `next` or the sign-in form.
- On the shared host the master's own `/login`, `/oauth/login` and `/logout` send people to the auth path. On a host of its own (served at the root), it signs people in there as a standalone app would, and the token is set for that host.

## Slave

- Identity of every request comes from the token. With a valid, fresh token the slave finds its own account for the master's `sub`:
  1. the account whose `users.auth_subject` is `sub`;
  2. else an account not linked yet with the same email (case-insensitive), then with the same username (case-insensitive), which is linked from then on;
  3. else a new account: username from the token (made unique), no password of its own, plus whatever a new account needs in the app (Ophir Cards: the default watchlists).
  Display name and role are taken from the token on every request, the email when the token has one.
- No valid token: pages redirect to the slave's `/login`, which redirects to `<auth path>/login?next=<slave path>/`; API calls answer 401 and the frontend goes to `/login` the same way.
- `/login`, `/oauth/login` (GET) and `/logout` (POST, as 307 so it stays a POST) redirect to the same routes under the auth path.
- Accounts, passwords and SSO links are the master's: the slave refuses changes to the own account (`PATCH /api/account`, password, SSO unlink) and to accounts in the admin area (create, change, delete) with 409. Its frontend shows these as managed by the master (bootstrap: `auth: {mode, base}`).
- A database column `users.auth_subject` (`TEXT NOT NULL DEFAULT ''`, unique where not empty) holds the link, separate from the SSO columns, so an app can go back to `standalone` without losing SSO links.

## Implementing it in another Ophir app

1. Copy `ophir_auth.py` unchanged.
2. Read `OPHIR_AUTH_MODE`, `OPHIR_AUTH_PATH`, `OPHIR_AUTH_HOME` (through the config module, so tests can switch them).
3. Add the `users.auth_subject` migration.
4. Port `ophir_cards/shared_auth.py`: the `before_request` hook that sets `session["user_id"]` from the token (master: `sub` is the account id; slave: `local_account()`), the 404 for app routes under the auth path, the master's `after_request` that renews and clears the token, `auth_base()`, `next_target()`, `to_auth_path()`, `refuse_when_slave()`.
5. In the sign-in routes: redirect to the auth path when `signs_in_elsewhere()`; end sign-ins at `next_target()`; keep `next` through the SSO round trip in the session; link-flow results go to `next` with `linked=1`/`oauth_error=<code>`.
6. Refuse account changes in slave mode; show them as managed by the master in the frontend.
7. Tests as in `tests/test_shared_auth.py`.
