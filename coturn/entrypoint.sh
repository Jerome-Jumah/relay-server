#!/bin/sh
set -eu
umask 077
python3 /opt/relay/configure.py coturn
exec turnserver -c /etc/coturn/turnserver.conf
