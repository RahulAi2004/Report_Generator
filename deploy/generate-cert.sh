#!/usr/bin/env bash
# Self-signed certificate for internal deployment.
# For anything reachable from outside the office, use a real certificate
# (Let's Encrypt / your corporate CA) instead -- browsers will warn on this one.
set -euo pipefail
cd "$(dirname "$0")/certs"
HOST="${1:-bi.internal}"
openssl req -x509 -nodes -newkey rsa:2048 -days 825 \
  -keyout privkey.pem -out fullchain.pem \
  -subj "/CN=${HOST}" \
  -addext "subjectAltName=DNS:${HOST},DNS:localhost,IP:127.0.0.1"
chmod 600 privkey.pem
echo "Wrote certs for ${HOST}. Now uncomment the HTTPS block in deploy/nginx.conf."
