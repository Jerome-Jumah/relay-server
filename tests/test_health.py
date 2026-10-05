import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import health

# Deliberately non-production sentinel; it must never reach diagnostic output.
PASSWORD = 'credential-leak-sentinel'
RESPONSES = {
    'status': 'UP 0 years, 0 days, 0 hours, 1 minute\n',
    'sofia status': 'internal profile sip:mod_sofia@172.18.0.2:5060 RUNNING (0)\n',
    'module_exists mod_opus': 'true\n',
    'show codecs': 'codec,OPUS\ncodec,VP8\n',
}


class HealthTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {'ESL_PASSWORD': PASSWORD}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def invoke(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(sys, 'argv', ['health.py', 'freeswitch']), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = health.main()
        self.assertNotIn(PASSWORD, stdout.getvalue() + stderr.getvalue())
        self.assertEqual(stdout.getvalue(), '')
        return code, stderr.getvalue()

    def response(self, args, **kwargs):
        self.assertFalse(kwargs['check'])
        self.assertTrue(kwargs['capture_output'])
        return subprocess.CompletedProcess(args, 0, RESPONSES[args[-1]], '')

    def test_healthy_esl_profile_and_codecs_without_ws_probe(self):
        with patch.object(health.subprocess, 'run', side_effect=self.response) as run, \
                patch.object(health.socket, 'create_connection') as connect:
            self.assertEqual(self.invoke(), (0, ''))
        connect.assert_not_called()
        self.assertEqual([call.args[0][-1] for call in run.call_args_list],
                         list(RESPONSES))

    def test_nonzero_exit_does_not_expose_subprocess_output(self):
        result = subprocess.CompletedProcess(['fs_cli', '-p', PASSWORD], 1,
                                             PASSWORD, PASSWORD)
        with patch.object(health.subprocess, 'run', return_value=result):
            self.assertEqual(self.invoke(),
                             (1, 'Health check failed: FreeSWITCH ESL command failed\n'))

    def test_error_response_is_sanitized(self):
        result = subprocess.CompletedProcess([], 0, '-ERR ' + PASSWORD, PASSWORD)
        with patch.object(health.subprocess, 'run', return_value=result):
            self.assertEqual(self.invoke()[0], 1)

    def test_exceptions_never_expose_command_arguments_or_output(self):
        failures = [
            subprocess.TimeoutExpired(['fs_cli', '-p', PASSWORD], 3,
                                      output=PASSWORD, stderr=PASSWORD),
            subprocess.CalledProcessError(1, ['fs_cli', '-p', PASSWORD],
                                          output=PASSWORD, stderr=PASSWORD),
            OSError(PASSWORD),
            RuntimeError(PASSWORD),
        ]
        for failure in failures:
            with self.subTest(kind=type(failure).__name__), \
                    patch.object(health.subprocess, 'run', side_effect=failure):
                code, message = self.invoke()
                self.assertEqual(code, 1)
                self.assertTrue(message.startswith('Health check failed: '))

    def test_missing_password_has_safe_diagnostic(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(self.invoke(),
                             (1, 'Health check failed: ESL password is not configured\n'))

    def test_process_status_and_profile_are_required(self):
        for command, response, diagnostic in [
            ('status', '', 'FreeSWITCH status response is invalid'),
            ('sofia status', 'internal profile sip:local STOPPED',
             'internal SIP profile is not running'),
            ('module_exists mod_opus', 'false', 'Opus module is unavailable'),
            ('show codecs', 'codec,OPUS', 'VP8 codec is unavailable'),
        ]:
            with self.subTest(command=command), patch.dict(RESPONSES, {command: response}), \
                    patch.object(health.subprocess, 'run', side_effect=self.response):
                self.assertEqual(self.invoke(),
                                 (1, f'Health check failed: {diagnostic}\n'))


if __name__ == '__main__':
    unittest.main()
