#!/bin/sh
set -eu
umask 077
python3 /opt/relay/configure.py freeswitch
exec freeswitch -nc -nf -nonat -conf /etc/freeswitch \
  -db /var/lib/freeswitch/db -log /tmp -run /var/lib/freeswitch/run \
  -certs /var/lib/freeswitch/tls
