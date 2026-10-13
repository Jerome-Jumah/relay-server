#!/bin/sh
set -eu
umask 077
# Refuse missing/unreadable certificates instead of generating a self-signed WSS cert.
if [ -r /etc/freeswitch/wss/wss.pem ] && [ -s /etc/freeswitch/wss/wss.pem ]; then
  :
else
  echo 'WSS certificate missing or unreadable: mount a combined wss.pem in /etc/freeswitch/wss' >&2
  exit 1
fi
python3 /opt/relay/configure.py freeswitch
exec freeswitch -nc -nf -nonat -conf /etc/freeswitch \
  -db /var/lib/freeswitch/db -log /tmp -run /var/lib/freeswitch/run \
  -certs /var/lib/freeswitch/tls
