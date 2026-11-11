#!/usr/bin/env bash
# Generate the Fleet API command-signing key pair and a TLS cert for the local signing proxy.
set -euo pipefail
cd "$(dirname "$0")/../config"
if [[ ! -f fleet-key.pem ]]; then
  openssl ecparam -name prime256v1 -genkey -noout -out fleet-key.pem
  openssl ec -in fleet-key.pem -pubout -out fleet-public-key.pem
  chmod 600 fleet-key.pem
  echo "created fleet-key.pem / fleet-public-key.pem"
fi
if [[ ! -f tls-key.pem ]]; then
  openssl req -x509 -nodes -newkey ec \
    -pkeyopt ec_paramgen_curve:secp384r1 -pkeyopt ec_param_enc:named_curve \
    -subj '/CN=localhost' -keyout tls-key.pem -out tls-cert.pem -sha256 -days 3650 \
    -addext "extendedKeyUsage = serverAuth" -addext "keyUsage = digitalSignature, keyCertSign, keyAgreement" \
    -addext "subjectAltName = DNS:localhost"
  chmod 600 tls-key.pem
  echo "created tls-key.pem / tls-cert.pem"
fi
