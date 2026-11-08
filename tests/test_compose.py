import os
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


def extract_section(yaml_text, service_name, section_name):
    """Extract lines for a specific section (e.g. 'ports:') under a service."""
    lines = yaml_text.splitlines()
    in_service = False
    in_section = False
    section_lines = []
    service_indent = 0
    section_indent = 0

    for line in lines:
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        if not stripped or stripped.startswith('#'):
            continue
        if re.match(rf'^{service_name}:', stripped):
            in_service = True
            service_indent = indent
            continue
        if in_service and indent <= service_indent and re.match(r'^[a-zA-Z0-9_-]+:', stripped):
            break
        if in_service and re.match(rf'^{section_name}:', stripped):
            in_section = True
            section_indent = indent
            continue
        if in_section:
            if indent <= section_indent:
                break
            section_lines.append(stripped)
    return section_lines


class ComposeTests(unittest.TestCase):
    def setUp(self):
        self.coolify_compose = ROOT / 'compose.yaml'
        self.standalone_compose = ROOT / 'compose.standalone.yaml'
        self.assertTrue(self.coolify_compose.is_file(), "compose.yaml must exist")
        self.assertTrue(self.standalone_compose.is_file(), "compose.standalone.yaml must exist")
        self.coolify_text = self.coolify_compose.read_text()
        self.standalone_text = self.standalone_compose.read_text()

    def test_both_define_core_services(self):
        for name, text in [('compose.yaml', self.coolify_text),
                           ('compose.standalone.yaml', self.standalone_text)]:
            with self.subTest(file=name):
                self.assertIn('services:', text)
                self.assertIn('freeswitch:', text)
                self.assertIn('coturn:', text)
                self.assertIn('freeswitch-state:', text)
                self.assertIn('SYS_NICE', text)

    def test_security_constraints_and_no_private_port_leakage(self):
        for name, text in [('compose.yaml', self.coolify_text),
                           ('compose.standalone.yaml', self.standalone_text)]:
            with self.subTest(file=name):
                fs_ports = extract_section(text, 'freeswitch', 'ports')
                # Plain WS port 5066 must never be mapped to host ports
                self.assertFalse(any('5066' in line for line in fs_ports),
                                 f"5066 found mapped in {name} ports")
                # ESL administrative port 8021 must never be mapped to host ports
                self.assertFalse(any('8021' in line for line in fs_ports),
                                 f"8021 found mapped in {name} ports")
                # Standard unencrypted SIP port 5060 must not be mapped to host
                self.assertFalse(any('5060' in line for line in fs_ports),
                                 f"5060 found mapped in {name} ports")

    def test_standalone_maps_wss_and_media_ports(self):
        fs_ports = extract_section(self.standalone_text, 'freeswitch', 'ports')
        # Standalone must explicitly map WSS port (7443 or configurable WSS_PORT)
        self.assertTrue(any('7443' in line and '${WSS_PORT:-7443}' in line for line in fs_ports),
                        "WSS_PORT mapping missing from standalone freeswitch ports")
        # Standalone must map RTP range
        self.assertTrue(any('RTP_START' in line and 'RTP_END' in line for line in fs_ports),
                        "RTP range mapping missing from standalone freeswitch ports")

        coturn_ports = extract_section(self.standalone_text, 'coturn', 'ports')
        self.assertTrue(any('TURN_PORT' in line and '/udp' in line for line in coturn_ports))
        self.assertTrue(any('TURN_PORT' in line and '/tcp' in line for line in coturn_ports))
        self.assertTrue(any('TURN_MIN_PORT' in line and 'TURN_MAX_PORT' in line for line in coturn_ports))

    def test_certificate_bind_mount_behavior(self):
        # Standalone must default to ./certs/wss with WSS_CERT_DIR override
        self.assertIn('source: ${WSS_CERT_DIR:-./certs/wss}', self.standalone_text)
        self.assertIn('target: /etc/freeswitch/wss', self.standalone_text)
        self.assertIn('read_only: true', self.standalone_text)
        self.assertIn('create_host_path: false', self.standalone_text)

        # Coolify compose must preserve read-only and create_host_path: false
        self.assertIn('target: /etc/freeswitch/wss', self.coolify_text)
        self.assertIn('read_only: true', self.coolify_text)
        self.assertIn('create_host_path: false', self.coolify_text)

    def test_no_hardcoded_coolify_application_id(self):
        self.assertNotIn('coolify.applicationId', self.coolify_text)
        self.assertNotIn('coolify.applicationId', self.standalone_text)

    def test_standalone_has_no_coolify_or_traefik_labels(self):
        self.assertNotIn('coolify.managed', self.standalone_text)
        self.assertNotIn('coolify.type', self.standalone_text)
        self.assertNotIn('traefik.enable', self.standalone_text)
        self.assertNotIn('traefik.tcp.routers', self.standalone_text)

    def test_coolify_preserves_traefik_passthrough_labels(self):
        self.assertIn('traefik.enable=true', self.coolify_text)
        self.assertIn('traefik.tcp.routers.relay-sip.tls.passthrough=true', self.coolify_text)
        self.assertIn('traefik.tcp.services.relay-sip.loadbalancer.server.port=7443', self.coolify_text)


if __name__ == '__main__':
    unittest.main()
