# Deployment: now on the workstation, later on the Docker host

Two deployments run **the same code**, differing only in configuration. Nothing
in the codebase knows which it is: every path arrives by environment variable,
which is what makes the move a matter of mounts and values rather than a
rewrite.

Worth separating the two, because the decisions differ.

## Now: workstation, data on the C: drive

```
OBDI_DB_PATH=./data/obdi/store.sqlite3
OBDI_CONNECTION_STORE=../obdi-secrets/obdi/connections.json
OBDI_ACCOUNT_MAP=./data/obdi/accounts.json
TRUELAYER_CLIENT_SECRET_FILE=../obdi-secrets/truelayer/<the secret file>
TRUELAYER_REDIRECT_URI=https://console.truelayer.com/redirect-page
```

Run commands from the virtual environment. This is the right place to be while
parsers are still being checked against real exports, because a failing import
is a file you can look at immediately.

**Keep `../obdi-secrets` and `./data` out of every repository**, and in
the backup scheme. The connection store holds refresh tokens; the transaction
store holds your financial history. Neither is replaceable from anywhere else.

## Later: Docker host, data under /srv/appdata

```
/srv/appdata/obdi/data      the store, connections and account map
/srv/appdata/obdi/secrets   token files, mounted READ ONLY
```

The container reads token files and must never rewrite one, so the secrets
mount is `:ro`. Both services run as a non-root user.

Bring it up with the stack conventions already in use: `.env` rendered from
Ansible Vault, the service bound to `127.0.0.1`, and exposure handled by
Tailscale Serve rather than published to the LAN.

### The redirect URI changes, and that matters

On the workstation the redirect is the provider's hosted page and you paste a
URL back. On the Docker host it becomes the Serve hostname:

```
TRUELAYER_REDIRECT_URI=https://obdi.<tailnet>.ts.net/callback
```

Register that with the provider **as well as**, not instead of, the existing
one — up to several redirect URIs are allowed, and keeping both means the
command line route still works if the container is down. Console changes take
up to 15 minutes to propagate.

Existing connections are unaffected: refreshing a token does not involve the
redirect URI. Only new authorisations use it.

### Moving the data across

The store and the connection file move as plain files; nothing re-derives from
scratch and no re-authorisation is needed.

1. Stop anything that writes: the scheduler, and any command mid-run.
2. Copy `store.sqlite3` and `accounts.json` to `/srv/appdata/obdi/data/`.
3. Copy `connections.json` to the same place, and the token files to
   `/srv/appdata/obdi/secrets/`.
4. Bring the stack up and check `obdi connections` reports the same consent
   clocks. If it reports none, the connection store is in the wrong place.

Consent clocks keep running across the move. The move does not reset them.

## The scheduled pull

Six hours, and that is not a tuning choice. Many banks cap unattended data
fetches at four a day, and the aggregator's own polling runs on the same cycle,
so a shorter interval buys nothing and risks a rate limit. A pull that fails is
logged and the loop continues rather than stopping the schedule, because the
commonest cause is a single expired consent that should not halt the others.

## Exposure

The published port is pinned to loopback in both deployments. That is
deliberate and worth not "fixing": the page can begin a bank authorisation, so
it must not answer to everything that can reach the host.

Reaching it from a phone is Tailscale Serve's job, which gives a real
certificate and a tailnet-only address. Note that enabling HTTPS on a Tailscale
hostname publishes that hostname to public Certificate Transparency logs — the
name leaks, never the traffic — so keep the name dull.
