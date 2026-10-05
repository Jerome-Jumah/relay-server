#!/usr/bin/env python3
"""Local readiness checks; external media connectivity needs a real call."""
import os
import re
import socket
import struct
import subprocess
import sys


class HealthCheckError(Exception):
    """A diagnostic containing only a fixed, safe message."""


def fs_command(command):
    password = os.environ.get('ESL_PASSWORD')
    if not password:
        raise HealthCheckError('ESL password is not configured')
    try:
        result = subprocess.run(
            ['fs_cli', '-H', '127.0.0.1', '-p', password, '-x', command],
            capture_output=True, text=True, timeout=3, check=False,
        )
    except subprocess.TimeoutExpired:
        raise HealthCheckError('FreeSWITCH ESL command timed out') from None
    except OSError:
        raise HealthCheckError('Unable to execute FreeSWITCH CLI') from None
    except subprocess.SubprocessError:
        raise HealthCheckError('FreeSWITCH CLI execution failed') from None
    # Output and exception text may contain credentials. Print neither.
    if result.returncode != 0 or '-ERR' in result.stdout:
        raise HealthCheckError('FreeSWITCH ESL command failed')
    return result.stdout


def freeswitch():
    if not fs_command('status').lstrip().startswith('UP '):
        raise HealthCheckError('FreeSWITCH status response is invalid')
    if not re.search(r'internal\s+profile\s+\S+\s+RUNNING', fs_command('sofia status')):
        raise HealthCheckError('internal SIP profile is not running')
    if fs_command('module_exists mod_opus').strip() != 'true':
        raise HealthCheckError('Opus module is unavailable')
    if 'VP8' not in fs_command('show codecs'):
        raise HealthCheckError('VP8 codec is unavailable')


def coturn():
    transaction = os.urandom(12)
    packet = struct.pack('!HHI', 0x0001, 0, 0x2112A442) + transaction
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(2)
        sock.sendto(packet, ('127.0.0.1', int(os.environ.get('TURN_PORT', '3478'))))
        reply, _ = sock.recvfrom(2048)
    if len(reply) < 20 or reply[:2] != b'\x01\x01' or reply[4:8] != packet[4:8] or reply[8:20] != transaction:
        raise HealthCheckError('invalid STUN binding response')


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {'freeswitch', 'coturn'}:
        print('Health check failed: expected freeswitch or coturn', file=sys.stderr)
        return 1
    try:
        {'freeswitch': freeswitch, 'coturn': coturn}[sys.argv[1]]()
    except HealthCheckError as error:
        print(f'Health check failed: {error}', file=sys.stderr)
        return 1
    except OSError:
        print('Health check failed: local probe network error', file=sys.stderr)
        return 1
    except Exception:
        print('Health check failed: unexpected local probe failure', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
