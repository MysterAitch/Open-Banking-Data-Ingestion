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

The services (the web page and scheduler from `Dockerfile`, and the applier from
`applier/Dockerfile`) run as a non-root user, and the published port stays on loopback.
That last part is deliberate and worth not "fixing": the page can begin a bank
authorisation, so it must not answer everything that can reach the host.
Exposure belongs to whatever you already use for private access.

Several instances can run side by side (the live one, a restore target, a
synthetic one), and they render identically. Set `OBDI_INSTANCE_LABEL` and
`OBDI_INSTANCE_ROLE` on each: role `production` shows nothing, so that its
absence is what marks the real one; any other role shows a boxed banner and a
title prefix on every page; an instance with neither set says it is not
identified rather than guessing.

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
2. Take the store with `obdi backup <destination>` and move THAT file, rather
   than copying `store.sqlite3` yourself. This is not a style preference - see
   below.
3. Copy `accounts.json` into the data volume, `connections.json` into the
   credentials volume, and the token files into the secrets volume.
4. Bring it up and check `obdi connections` reports the same consent clocks. If
   it reports none, the connection store is in the wrong place.

Consent clocks keep running across the move; it does not reset them.

**Why not simply copy the file.** The store runs with write-ahead logging, so
recently committed rows live in the `-wal` sidecar until a checkpoint folds them
back in. Copying `store.sqlite3` alone leaves them there - measured here at 600 to
750 committed rows short on every trial, and every one of those short copies
passed SQLite's own `integrity_check`. The copy is not corrupt. It is valid,
openable, and quietly missing the newest work, which is the worst of the available
failures because nothing about it looks wrong until the day you need it.

`obdi backup` takes the copy through the application and refuses to hand you one
whose row counts do not match the live store, table by table; `obdi verify-backup`
re-runs that same comparison on a copy you already have, which only means
anything while the store still holds what it held when the copy was taken. For
an ARCHIVE, use **`obdi inspect-backup <file>`**: it reports what the file holds
- integrity, tables present and absent, rows per table - and asks the live store
nothing. It deliberately does not claim the file is complete, because no file can
testify to that on its own. Do not read a `verify-backup` refusal on an old
backup as corruption: a copy short by a month and a copy short by a lost
write-ahead log look identical in the totals, and the refusal says so. **`obdi restore <backup> --to <path>`** turns
one back into a store, which is the half that is only ever tested by doing it: it
refuses an untrustworthy copy before touching anything, moves any existing store
aside as `.replaced` rather than deleting it, takes the `-wal` and `-shm`
sidecars with the database they belong to, and opens the result through the
application so what it reports is a store this release can use rather than a file
that exists. Add `--replace` to go ahead when something is already there. If you must move files by hand, take
`store.sqlite3`, `store.sqlite3-wal` and `store.sqlite3-shm` together with every
writer stopped - but prefer the command, which checks its own work.

**One layer regenerates and one does not, and only the second needs you.** Raw
artefacts can be fetched again and statements re-imported; `obdi export-raw`
projects them onto the filesystem for eyes and ordinary tools. Categories you
typed, accounts you declared and review decisions you made cannot be recreated by
any amount of fetching - the evidence behind a review decision is precisely what
could not settle itself. `obdi export-declared <dir>` writes that layer out,
keyed on content identity rather than on entity ids, which are re-minted by every
rebuild. Worth running before anything drastic, and cheap enough to run often.

**Regenerating beats copying** where you can. Provider secrets can usually be
re-issued in a console, and bank connections re-authorised from a phone in a
couple of minutes. Nothing transits, and you rotate every credential as a side
effect.

## The scheduled pull

Six hours (`OBDI_PULL_INTERVAL_SECONDS`, default 21600), and that is not a tuning
choice. Many banks cap unattended data fetches at four a day, and an aggregator's
own polling runs on the same cycle, so a shorter interval buys nothing and risks
a rate limit. The loop in `compose.yaml` runs a bare `obdi pull`, which fetches
every stored connection (card accounts included) and Starling when its token
resolves, then `obdi pair-transfers` and `obdi export-raw`. A failed pull is
logged and the loop continues rather than halting the schedule, because the
commonest cause is a single expired consent that should not stop the others.

A pull labelled scheduled (`OBDI_TRIGGER=scheduled`) is spaced from the previous
scheduled one by 90 per cent of the interval unless
`OBDI_PULL_MIN_INTERVAL_SECONDS` says otherwise (0 switches it off). A cycle that
starts early, as after a deploy restarts the container, waits for its slot and
then pulls, rather than giving up and leaving a whole interval of silence. A
cycle that meets a rebuild or an attended post-authorisation backfill holding its
lease waits briefly for it and then skips the pull for that cycle.

`obdi alert` is meant to run last in the cycle (the local `compose.yaml` loop
does not include it; a deployment adds it). It prints every finding, sends a
notification when one appears and again when it clears (`OBDI_NTFY_URL`, else the
log is the channel), and pings `OBDI_HEARTBEAT_URL` when the cycle reached it, so
a cycle that never finishes is the one failure it can report by silence.
