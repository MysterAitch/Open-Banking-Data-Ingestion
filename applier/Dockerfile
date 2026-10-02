# The one Node process in the project, containerised: it embeds Actual's own
# budget engine, so it is version-pinned to the server and nothing else
# shares its image. It talks to the Python containers only through JSON
# files on the shared /data volume - no sockets, no APIs between them.
FROM node:22-slim

RUN useradd --create-home --uid 10001 obdi
WORKDIR /app

COPY package.json package-lock.json ./
# --ignore-scripts: better-sqlite3 13 ships its native binaries inside the
# package, yet npm 10 (the one in this base image) still runs an implicit
# node-gyp rebuild for it, which has no compiler to run on in a slim image
# and fails the build. No other production dependency has an install script.
# The import check below proves the binding loads.
RUN npm ci --omit=dev --ignore-scripts

COPY audit.mjs empty.mjs envelope.mjs lease.mjs lib.mjs marker.mjs transfers.mjs turn.mjs watcher.mjs apply.mjs ./

# Prove the whole import graph resolves at BUILD time. A file missing from
# the COPY list above otherwise surfaces only as a crash loop on the host -
# which is exactly how audit.mjs's absence was discovered. The watcher's
# entry point is guarded, so importing it starts nothing.
RUN node -e "await import('./watcher.mjs'); await import('./apply.mjs');"

RUN mkdir -p /data /secrets && chown -R obdi:obdi /data
USER obdi

ENV OBDI_ACTUAL_DIR=/data/actual
CMD ["node", "watcher.mjs"]
