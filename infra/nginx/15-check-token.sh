#!/bin/sh
set -eu
# Restrict characters before envsubst interpolates the token into nginx syntax.
case "${BACKEND_DEMO_TOKEN:-}" in
  ''|*[!a-zA-Z0-9_-]*) echo 'BACKEND_DEMO_TOKEN must contain only letters, digits, underscore or hyphen' >&2; exit 1;;
esac
[ "${#BACKEND_DEMO_TOKEN}" -ge 20 ] || { echo 'BACKEND_DEMO_TOKEN must be at least 20 characters' >&2; exit 1; }
