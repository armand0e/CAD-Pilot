#!/bin/sh
set -eu
umask 077
# Fail startup with an actionable error if this Docker host blocks nested sandboxes.
if ! bwrap --unshare-all --ro-bind /usr /usr --ro-bind /lib /lib --ro-bind /lib64 /lib64 --proc /proc --dev /dev /usr/bin/true; then
    echo 'CAD sandbox unavailable: enable unprivileged user namespaces and the Compose security_opt settings.' >&2
    exit 1
fi
if [ "${CADPILOT_AUTH_ENABLED:-0}" = 1 ]; then
    python -m server.auth bootstrap
fi
exec "$@"
