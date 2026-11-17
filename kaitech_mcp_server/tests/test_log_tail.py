import os
import tempfile
from datetime import datetime, timedelta
from unittest.mock import patch

from .common import McpCase, new_token, new_user

LOG_FILE_PATH = 'odoo.addons.kaitech_mcp_server.services.log_tools.log_file_path'
SECRETS = (
    'abc123sessionvalue', 'FAKEbearerValue1234567890', 'hunter2secret', 'cookieSessionValue', 'k9fakekey',
    'sk-FAKEKEY1234567890abcd', 'ghp_FAKE1234567890abcdefgh', 'topsecretdb',
    'Zm9vYmFyYmF6cXV4MTIzNDU2Nzg5MGFiY2RlZmdoaWprbG1u', 'tokenSecretValue',
)


def stamp(minutes_ago):
    return (datetime.now() - timedelta(minutes=minutes_ago)).strftime('%Y-%m-%d %H:%M:%S,123')


class TestServerLogTail(McpCase):

    def setUp(self):
        super().setUp()
        with self.shared_env() as env:
            self.admin = new_user(env, 'mcp_log_admin', groups=('base.group_user', 'base.group_system'))
            self.writer = new_user(env, 'mcp_log_writer', groups=('base.group_user', 'base.group_system'))
            self.user = new_user(env, 'mcp_log_user')
            _token, self.admin_raw = new_token(env, self.admin, scope='admin')
            _token, self.writer_raw = new_token(env, self.writer, scope='write')
            _token, self.user_raw = new_token(env, self.user, scope='admin')
        handle, self.path = tempfile.mkstemp(suffix='.log')
        self.addCleanup(os.unlink, self.path)
        with os.fdopen(handle, 'w') as log:
            log.write('\n'.join([
                f'{stamp(90)} 11 INFO db odoo.modules.loading: an old line',
                f'{stamp(2)} 11 INFO db werkzeug: 127.0.0.1 "GET /web?session_id=abc123sessionvalue HTTP/1.1" 200',
                f'{stamp(2)} 11 ERROR db odoo.addons.kaitech_mcp_server.x: boom Authorization: Bearer FAKEbearerValue1234567890',
                'Traceback (most recent call last):',
                '  login with password=hunter2secret failed',
                f"{stamp(1)} 11 WARNING db odoo.http: {{'Cookie': 'session_id=cookieSessionValue', 'api_key': 'k9fakekey'}}"
                ' sk-FAKEKEY1234567890abcd ghp_FAKE1234567890abcdefgh db_password = topsecretdb'
                ' "access_token": "tokenSecretValue"',
                f'{stamp(0)} 11 INFO db odoo.modules: random Zm9vYmFyYmF6cXV4MTIzNDU2Nzg5MGFiY2RlZmdoaWprbG1u',
            ]) + '\n')

    def tail(self, raw=None, **arguments):
        with patch(LOG_FILE_PATH, return_value=self.path):
            return self.call(raw or self.admin_raw, 'server_log_tail', arguments)

    def test_lines_come_back_with_every_planted_secret_redacted(self):
        result = self.tail()
        self.assertFalse(result['isError'], result)
        answer = result['structuredContent']
        self.assertEqual(answer['count'], 7)
        self.assertFalse(answer['truncated'])
        text = '\n'.join(answer['lines'])
        for secret in SECRETS:
            self.assertNotIn(secret, text)
        self.assertGreaterEqual(text.count('[REDACTED]'), len(SECRETS))
        self.assertIn('an old line', text)

    def test_filters(self):
        errors = self.tail(level='ERROR')['structuredContent']['lines']
        self.assertEqual(len(errors), 3)
        self.assertIn('Traceback', errors[1])
        self.assertEqual(self.tail(logger='odoo.addons.kaitech_mcp_server')['structuredContent']['lines'], errors)
        self.assertEqual(self.tail(level='warning')['structuredContent']['count'], 4)
        self.assertEqual(self.tail(since_minutes=30)['structuredContent']['count'], 6)
        self.assertEqual(self.tail(grep='RANDOM')['structuredContent']['count'], 1)

    def test_grep_cannot_probe_a_redacted_secret(self):
        self.assertEqual(self.tail(grep='hunter2')['structuredContent']['count'], 0)

    def test_a_cap_below_the_matches_marks_the_answer_truncated(self):
        answer = self.tail(lines=2)['structuredContent']
        self.assertEqual((answer['count'], answer['truncated']), (2, True))
        self.assertIn('random', answer['lines'][-1])

    def test_a_write_scope_token_is_refused(self):
        result = self.tail(raw=self.writer_raw)
        self.assertTrue(result['isError'])
        self.assertIn("requires 'admin' scope", result['structuredContent']['error'])

    def test_an_admin_scope_token_of_a_non_administrator_is_refused(self):
        result = self.tail(raw=self.user_raw)
        self.assertIn('Settings administrator', result['structuredContent']['error'])

    def test_no_log_file_is_said_so(self):
        with patch(LOG_FILE_PATH, return_value=None):
            result = self.call(self.admin_raw, 'server_log_tail', {})
        self.assertIn('no log file is configured', result['structuredContent']['error'])
