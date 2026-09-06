#!/bin/sh
set -e

CERT_DIR=/etc/nginx/certs
CERT="$CERT_DIR/server.crt"
KEY="$CERT_DIR/server.key"
HOST="${TLS_HOST:-localhost}"

mkdir -p "$CERT_DIR"

# Zertifikat liegt im Volume und überlebt Neustarts. Das ist wichtig: ein bei
# jedem Start neu erzeugtes Zertifikat müsste im Browser jedes Mal neu
# akzeptiert werden.
if [ ! -f "$CERT" ] || [ ! -f "$KEY" ]; then
    echo "Erzeuge self-signed Zertifikat für '$HOST' ..."
    openssl req -x509 -nodes -newkey rsa:2048 -days 3650 \
        -keyout "$KEY" -out "$CERT" \
        -subj "/CN=$HOST" \
        -addext "subjectAltName=DNS:$HOST,DNS:localhost,IP:127.0.0.1" \
        2>/dev/null
    echo "Zertifikat erstellt (gültig 10 Jahre, CN=$HOST)."
else
    echo "Vorhandenes Zertifikat wird genutzt ($CERT)."
fi

exec nginx -g 'daemon off;'
