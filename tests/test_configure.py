import hashlib
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import configure

ROOT = Path(__file__).resolve().parents[1]
ENV = {
    'SIP_DOMAIN': 'sip.example.com', 'PUBLIC_IP': '203.0.113.10',
    'SIP_PASSWORD_SEED': 'a' * 64, 'ESL_PASSWORD': 'b' * 64,
    'SIP_EXT_START': '201', 'SIP_EXT_END': '204',
    'TURN_REALM': 'turn.example.com', 'TURN_USERNAME': 'relay',
    'TURN_PASSWORD': 'c' * 64,
}


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, ENV, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name)

    def render(self):
        configure.render_freeswitch(self.output, ROOT / 'freeswitch/conf')

    def test_rendered_profile_has_native_ws_and_wss(self):
        self.render()
        settings = {
            param.get('name'): param.get('value')
            for param in ET.parse(self.output / 'sip_profiles/internal.xml').findall('./settings/param')
        }
        self.assertEqual(settings['ws-binding'], ':5066')
        self.assertEqual(settings['wss-binding'], ':7443')
        self.assertEqual(settings['tls-cert-dir'], '/etc/freeswitch/wss')
        self.assertEqual(settings['tls'], 'false')  # SIP/TLS is independent of WSS.

    def test_range_authentication_and_dialplan_agree(self):
        self.render()
        users = ET.parse(self.output / 'directory.xml').findall('.//user')
        self.assertEqual([u.get('id') for u in users], ['201', '202', '203', '204'])
        hashes = set()
        for user in users:
            number = user.get('id')
            password = configure.sip_password(ENV['SIP_PASSWORD_SEED'], ENV['SIP_DOMAIN'], number)
            digest = user.find('./params/param').get('value')
            self.assertEqual(digest, hashlib.md5(f"{number}:sip.example.com:{password}".encode()).hexdigest())
            hashes.add(digest)
        self.assertEqual(len(hashes), 4)
        condition = ET.parse(self.output / 'dialplan/relay.xml').find('.//condition')
        pattern = condition.get('expression')
        for number in ['201', '202', '203', '204']:
            self.assertIsNotNone(re.fullmatch(pattern, number))
        for number in ['200', '205', '1001', 'sip:201@other.example']:
            self.assertIsNone(re.fullmatch(pattern, number))
        for file in self.output.rglob('*.xml'):
            self.assertNotIn('@@', file.read_text())
            self.assertEqual(file.stat().st_mode & 0o777, 0o600)

    def test_credentials_are_stable_and_scoped(self):
        args = ('a' * 64, 'sip.example.com', '201')
        self.assertEqual(configure.sip_password(*args), configure.sip_password(*args))
        self.assertNotEqual(configure.sip_password(*args), configure.sip_password('b' * 64, *args[1:]))
        self.assertNotEqual(configure.sip_password(*args), configure.sip_password(args[0], 'other.example.com', '201'))

    def test_compact_ranges_match_numeric_boundaries(self):
        ranges = [(1, 1), (1, 9), (8, 12), (95, 105), (999, 1001),
                  (1001, 11000), (123456789, 123466788),
                  (999990000, 999999999)]
        for start, end in ranges:
            with self.subTest(start=start, end=end):
                pattern = configure.extension_range_pattern(start, end)
                self.assertLess(len(pattern), 512)
                regex = re.compile('^(?:' + pattern + ')$')
                for number in range(start, end + 1):
                    self.assertIsNotNone(regex.fullmatch(str(number)))
                for destination in [str(start - 1), str(end + 1), '0' + str(start),
                                    '-1', '1.0', '+1', '١', 'sip:1001@example.com']:
                    self.assertIsNone(regex.fullmatch(destination))

    def test_small_ranges_exhaustively(self):
        for start in range(1, 31):
            for end in range(start, 121):
                regex = re.compile(configure.extension_range_pattern(start, end))
                matches = [n for n in range(0, 122) if regex.fullmatch(str(n))]
                self.assertEqual(matches, list(range(start, end + 1)))

    def test_large_directory_and_compact_dialplan_agree(self):
        with patch.dict(os.environ, {'SIP_EXT_START': '1001', 'SIP_EXT_END': '11000'}):
            self.render()
        users = ET.parse(self.output / 'directory.xml').findall('.//user')
        self.assertEqual(len(users), 10000)
        self.assertEqual([u.get('id') for u in users],
                         [str(n) for n in range(1001, 11001)])
        condition = ET.parse(self.output / 'dialplan/relay.xml').find('.//condition')
        pattern = condition.get('expression')
        self.assertLess(len(pattern), 512)
        regex = re.compile(pattern)
        for user in users:
            self.assertIsNotNone(regex.fullmatch(user.get('id')))
        self.assertIsNone(regex.fullmatch('1000'))
        self.assertIsNone(regex.fullmatch('11001'))

    def test_missing_or_unsafe_configuration_fails(self):
        for key, value in [('SIP_PASSWORD_SEED', ''), ('ESL_PASSWORD', 'ClueCon'),
                           ('SIP_DOMAIN', 'bad$${domain}'), ('SIP_EXT_START', '0201'),
                           ('SIP_EXT_END', '200'), ('SIP_EXT_END', '999999'),
                           ('RTP_START', '16385'), ('PUBLIC_IP', '0.0.0.0')]:
            with self.subTest(key=key, value=value), patch.dict(os.environ, {key: value}):
                with self.assertRaises(ValueError):
                    self.render()

    def test_turn_nat_and_authenticated_allocation_settings(self):
        target = self.output / 'turnserver.conf'
        configure.render_coturn(target, local_ip='172.18.0.3')
        text = target.read_text()
        self.assertIn('external-ip=203.0.113.10/172.18.0.3', text)
        self.assertIn('lt-cred-mech\n', text)
        self.assertIn('user=relay:' + 'c' * 64, text)
        self.assertIn('no-tls\n', text)
        self.assertNotIn('no-auth', text)
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_turn_port_overlap_is_rejected(self):
        for updates in [{'TURN_MIN_PORT': '16384', 'TURN_MAX_PORT': '16483'},
                        {'TURN_PORT': '49160'}, {'TURN_PORT': '16384'},
                        {'TURN_PASSWORD': 'x\nno-auth'}]:
            with self.subTest(updates=updates), patch.dict(os.environ, updates):
                with self.assertRaises(ValueError):
                    configure.render_coturn(self.output / 'turnserver.conf', '172.18.0.3')


if __name__ == '__main__':
    unittest.main()
