# The one Node process in the project, containerised: it embeds Actual's own
# budget engine, so it is version-pinned to the server and nothing else
# shares its image. It talks to the Python containers only through JSON
# files on the shared /data volume - no sockets, no APIs between them.
FROM node:22-slim

RUN useradd --create-home --uid 10001 obdi
WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci --omit=dev

COPY envelope.mjs lib.mjs watcher.mjs apply.mjs ./

RUN mkdir -p /data /secrets && chown -R obdi:obdi /data
USER obdi

ENV OBDI_ACTUAL_DIR=/data/actual
CMD ["node", "watcher.mjs"]
