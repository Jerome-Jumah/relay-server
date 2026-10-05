#!/usr/bin/env python3
"""Validate environment and generate configuration; no external dependencies."""
import hashlib
import hmac
import ipaddress
import os
from pathlib import Path
import re
import socket
import sys
import xml.etree.ElementTree as ET


def required(name):
    value = os.environ.get(name, "")
    if not value:
        raise ValueError(f"{name} is required")
    return value


def integer(name, default, minimum=1, maximum=65535):
    value = os.environ.get(name, str(default))
    if not re.fullmatch(r"[1-9][0-9]*", value):
        raise ValueError(f"{name} must be an integer without leading zeroes")
    number = int(value)
    if not minimum <= number <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return number


def secret(name):
    value = required(name)
    if not re.fullmatch(r"[0-9a-fA-F]{64}", value):
        raise ValueError(f"{name} must be 64 hex characters (openssl rand -hex 32)")
    return value


def domain(name):
    value = required(name)
    if len(value) > 253 or not all(
        re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", label)
        for label in value.split(".")
    ):
        raise ValueError(f"{name} must be a DNS hostname, without a scheme or port")
    return value.lower()


def public_ip():
    value = required("PUBLIC_IP")
    address = ipaddress.IPv4Address(value)
    if address.is_unspecified or address.is_multicast or address.is_loopback:
        raise ValueError("PUBLIC_IP must be a unicast public IPv4 address")
    return str(address)


def port_range(start_name, end_name, start_default, end_default):
    start = integer(start_name, start_default, minimum=1024)
    end = integer(end_name, end_default, minimum=1024)
    if start >= end:
        raise ValueError(f"{start_name} must be less than {end_name}")
    return start, end


def extension_settings():
    start = integer("SIP_EXT_START", 1001, maximum=999999999)
    end = integer("SIP_EXT_END", 1099, maximum=999999999)
    if end < start or end - start >= 10000:
        raise ValueError("SIP extension range must contain 1 to 10,000 users")
    return domain("SIP_DOMAIN"), secret("SIP_PASSWORD_SEED"), start, end


def sip_password(seed, sip_domain, extension):
    # Scope derivation to both domain and user; the seed never leaves the server.
    message = f"{extension}@{sip_domain}".encode()
    return hmac.new(bytes.fromhex(seed), message, hashlib.sha256).hexdigest()[:32]


def extension_range_pattern(start, end):
    """Cover decimal intervals by digit prefixes, not one alternative per user."""
    def digits(width):
        if width == 0:
            return ''
        return '[0-9]' if width == 1 else f'[0-9]{{{width}}}'

    def interval(low, high):
        if low == high:
            return low
        if low[0] == high[0]:
            return low[0] + interval(low[1:], high[1:])

        width = len(low) - 1
        first, last = int(low[0]), int(high[0])
        parts = []
        if low[1:] != '0' * width:
            parts.append(low[0] + interval(low[1:], '9' * width))
            first += 1
        upper_edge = high[1:] != '9' * width
        if upper_edge:
            last -= 1
        if first <= last:
            prefix = str(first) if first == last else f'[{first}-{last}]'
            parts.append(prefix + digits(width))
        if upper_edge:
            parts.append(high[0] + interval('0' * width, high[1:]))
        return parts[0] if len(parts) == 1 else '(?:' + '|'.join(parts) + ')'

    parts = []
    for width in range(len(str(start)), len(str(end)) + 1):
        low = max(start, 10 ** (width - 1))
        high = min(end, 10 ** width - 1)
        parts.append(interval(str(low), str(high)))
    return parts[0] if len(parts) == 1 else '(?:' + '|'.join(parts) + ')'


def write_private(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    path.chmod(0o600)


def render_freeswitch(output=Path('/etc/freeswitch'), templates=Path('/opt/relay/templates')):
    sip_domain, seed, start, end = extension_settings()
    rtp_start, rtp_end = port_range("RTP_START", "RTP_END", 16384, 16483)
    if rtp_start % 2 or rtp_end - rtp_start < 3:
        raise ValueError("RTP_START must be even and the range must have at least 4 ports")
    values = {
        "SIP_DOMAIN": sip_domain, "PUBLIC_IP": public_ip(),
        "ESL_PASSWORD": secret("ESL_PASSWORD"),
        "RTP_START": str(rtp_start), "RTP_END": str(rtp_end),
        "EXTENSION_RANGE_PATTERN": extension_range_pattern(start, end),
    }
    for template in templates.rglob('*.xml'):
        text = template.read_text()
        text = re.sub(r"@@([A-Z_]+)@@", lambda m: values[m[1]], text)
        ET.fromstring(text)
        write_private(output / template.relative_to(templates), text)

    root = ET.Element("include")
    directory = ET.SubElement(root, "domain", name=sip_domain)
    params = ET.SubElement(directory, "params")
    ET.SubElement(params, "param", name="dial-string", value=(
        "{presence_id=${dialed_user}@${dialed_domain}}"
        "${sofia_contact(${dialed_user}@${dialed_domain})}"
    ))
    users = ET.SubElement(ET.SubElement(ET.SubElement(directory, "groups"),
                                     "group", name="relay"), "users")
    for number in range(start, end + 1):
        extension = str(number)
        password = sip_password(seed, sip_domain, extension)
        digest = hashlib.md5(f"{extension}:{sip_domain}:{password}".encode()).hexdigest()
        user = ET.SubElement(users, "user", id=extension)
        params = ET.SubElement(user, "params")
        ET.SubElement(params, "param", name="a1-hash", value=digest)
        variables = ET.SubElement(user, "variables")
        for name, value in {
            "user_context": "relay", "accountcode": extension,
            "effective_caller_id_name": f"Relay {extension}",
            "effective_caller_id_number": extension,
        }.items():
            ET.SubElement(variables, "variable", name=name, value=value)
    ET.indent(root)
    write_private(output / 'directory.xml', ET.tostring(root, encoding='unicode') + '\n')


def render_coturn(output=Path('/etc/coturn/turnserver.conf'), local_ip=None):
    address = public_ip()
    realm = domain("TURN_REALM")
    username = required("TURN_USERNAME")
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", username):
        raise ValueError("TURN_USERNAME must contain only letters, digits, _ or -")
    password = secret("TURN_PASSWORD")
    port = integer("TURN_PORT", 3478, minimum=1024)
    start, end = port_range("TURN_MIN_PORT", "TURN_MAX_PORT", 49160, 49259)
    rtp_start, rtp_end = port_range("RTP_START", "RTP_END", 16384, 16483)
    if max(start, rtp_start) <= min(end, rtp_end) or start <= port <= end or rtp_start <= port <= rtp_end:
        raise ValueError("TURN listener, TURN relay range, and RTP range must not overlap")
    # Docker bridge NAT must advertise the public address with port-preserving mappings.
    local_ip = local_ip or socket.gethostbyname(socket.gethostname())
    lines = [
        f"listening-port={port}", "listening-ip=0.0.0.0", f"relay-ip={local_ip}",
        f"external-ip={address}/{local_ip}", f"realm={realm}",
        "fingerprint", "lt-cred-mech", f"user={username}:{password}",
        f"min-port={start}", f"max-port={end}",
        "no-tls", "no-dtls", "no-cli", "no-tcp-relay", "no-multicast-peers",
        "user-quota=20", "total-quota=100", "max-bps=1000000",
        "log-file=stdout", "simple-log",
    ]
    # Block TURN as a route to private services. Public FreeSWITCH remains reachable.
    for network in ["0.0.0.0-0.255.255.255", "10.0.0.0-10.255.255.255",
                    "100.64.0.0-100.127.255.255", "127.0.0.0-127.255.255.255",
                    "169.254.0.0-169.254.255.255", "172.16.0.0-172.31.255.255",
                    "192.168.0.0-192.168.255.255", "240.0.0.0-255.255.255.255"]:
        lines.append(f"denied-peer-ip={network}")
    write_private(output, '\n'.join(lines) + '\n')


def main():
    if len(sys.argv) < 2:
        raise ValueError("Usage: configure.py freeswitch|coturn|credentials EXTENSION")
    if sys.argv[1] == 'freeswitch':
        render_freeswitch()
    elif sys.argv[1] == 'coturn':
        render_coturn()
    elif sys.argv[1] == 'credentials' and len(sys.argv) == 3:
        sip_domain, seed, start, end = extension_settings()
        extension = sys.argv[2]
        if not extension.isascii() or not extension.isdigit() or str(int(extension)) != extension or not start <= int(extension) <= end:
            raise ValueError("Extension is outside the configured range")
        print(f"URI: sip:{extension}@{sip_domain}")
        print(f"Authorization username: {extension}")
        print(f"Password: {sip_password(seed, sip_domain, extension)}")
    else:
        raise ValueError("Usage: configure.py freeswitch|coturn|credentials EXTENSION")


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, OSError) as error:
        sys.exit(f"Configuration error: {error}")
