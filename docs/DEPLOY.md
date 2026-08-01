# Deployment: local first, container later

Both run **the same code**, differing only in configuration. Nothing in the
codebase knows which it is: every path arrives by environment variable, which is
what makes moving between them a matter of mounts and values rather than a
rewrite.

Worth separating the two, because the decisions differ.

## Local

```
OBDI_DB_PATH=./data/store.sqlite3
OBDI_ACCOUNT_MAP=./data/accounts.json
OBDI_CONNECTION_STORE=../obdi-secrets/connections.json
TRUELAYER_CLIENT_SECRET_FILE=../obdi-secrets/truelayer-client-secret
TRUELAYER_REDIRECT_URI=https://console.truelayer.com/redirect-page
```

Run from the virtual environment. This is the right place to be while parsers
are still being checked against real exports, because a failing import is a file
you can open immediately.

**Keep the data and credential directories outside the repository**, and in your
backup scheme. The connection store holds refresh tokens; the transaction store
holds your financial history. Neither is replaceable from anywhere else.

## Container

Three mounts, not one, because they have three different sensitivities:

```
/data          transaction history - copied, backed up, queried by tools
/secrets       provider credentials, mounted READ ONLY: read, never rewritten
/credentials   refresh tokens, written by the app as they rotate
```

The connection store must not live under `/data`. That volume exists to be
freely copied and queried, and putting bank credentials in it would make every
copy of the history also a copy of the credentials.

Both services run as a non-root user, and the published port stays on loopback.
That last part is deliberate and worth not "fixing": the page can begin a bank
authorisation, so it must not answer everything that can reach the host.
Exposure belongs to whatever you already use for private access.

Prefer pulling a published image over building on the host. Build failures then
surface in CI rather than during a deploy, and a published tag gives
update-watching something to compare. Render the configuration from whatever
secret store you run, so credentials are never hand-written on the host.

### The redirect URI changes, and that matters

Locally the redirect is the provider's hosted page and you paste a URL back. Once
the web interface runs somewhere your phone can reach, it becomes that address:

```
TRUELAYER_REDIRECT_URI=https://<your-host>/callback
```

Register it **as well as**, not instead of, the existing one - several redirect
URIs are allowed, and keeping both means the command-line route still works when
the service is down. Console changes can take about 15 minutes to propagate.

Existing connections are unaffected: refreshing a token does not involve the
redirect URI. Only new authorisations use it.

### Moving the data across

The store and the connection file move as plain files; nothing re-derives from
scratch and no re-authorisation is needed.

1. Stop anything that writes: the scheduler, and any command mid-run.
2. Copy `store.sqlite3` and `accounts.json` into the data volume.
3. Copy `connections.json` into the credentials volume, and the token files into
   the secrets volume.
4. Bring it up and check `obdi connections` reports the same consent clocks. If
   it reports none, the connection store is in the wrong place.

Consent clocks keep running across the move; it does not reset them.

**Regenerating beats copying** where you can. Provider secrets can usually be
re-issued in a console, and bank connections re-authorised from a phone in a
couple of minutes. Nothing transits, and you rotate every credential as a side
effect.

## The scheduled pull

Six hours, and that is not a tuning choice. Many banks cap unattended data
fetches at four a day, and an aggregator's own polling runs on the same cycle, so
a shorter interval buys nothing and risks a rate limit. A failed pull is logged
and the loop continues rather than halting the schedule, because the commonest
cause is a single expired consent that should not stop the others.
