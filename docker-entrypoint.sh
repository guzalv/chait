#!/bin/sh
set -e

# Make the data dir writable by the unprivileged app user regardless of how the
# mounted volume / host directory is owned (fresh named volumes inherit the
# image's ownership, but pre-existing volumes from older images and host
# bind-mounts can be owned by root or an arbitrary uid). We start as root only
# to fix ownership, then drop privileges to 'chait' to run the server.
if [ "$(id -u)" = "0" ]; then
    chown -R chait:chait "${CHAIT_DATA_DIR:-/data}" 2>/dev/null || true
    exec gosu chait "$@"
fi

# Already running as a non-root user (e.g. `docker run --user ...`): run as-is.
exec "$@"
