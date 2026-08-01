# Two deployments run the same code, differing only in configuration:
#
#   now      on the workstation, data on the C: drive, run from a virtualenv
#   later    on the Docker host, data under /srv/appdata, run from this image
#
# Nothing in the code knows which it is. Every path arrives by environment
# variable, which is what makes the move a matter of mounts and values rather
# than a rewrite.

FROM python:3.13-slim

# Non-root by default. This process can begin a bank authorisation and holds
# refresh tokens; there is no reason for it to be able to write anywhere else.
RUN useradd --create-home --uid 10001 obdi

WORKDIR /app

# Dependencies first, so a code change does not invalidate the layer.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

# Three mounts, not two, because they have three different sensitivities:
#   /data         transaction history - copied, backed up, queried by tools
#   /secrets      token files, READ ONLY: read, never rewritten
#   /credentials  refresh tokens, written by the app as it rotates them
#
# The connection store used to live in /data, which quietly reversed the
# separation the code is built around: that volume is meant to be freely
# copyable, and putting bank credentials in it would make every copy of the
# data a copy of the credentials.
RUN mkdir -p /data /secrets /credentials && chown -R obdi:obdi /data /credentials

USER obdi

ENV PYTHONUNBUFFERED=1 \
    OBDI_DB_PATH=/data/store.sqlite3 \
    OBDI_CONNECTION_STORE=/credentials/connections.json \
    OBDI_ACCOUNT_MAP=/data/accounts.json

EXPOSE 8080

# Bound to all interfaces INSIDE the container only; the published port is
# pinned to loopback in compose, and exposure beyond that is Tailscale Serve's
# job. Binding to 127.0.0.1 here would make the container unreachable.
CMD ["obdi", "serve", "--host", "0.0.0.0", "--port", "8080"]
