"""Offline tests for email-alert presets. No network, no real mail account, no Keychain access."""
import argparse
import io
import re
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

from getmeajob import jobfilters, notify, notify_cli
from getmeajob.config import ConfigurationError
from tests.test_job_filters import FilterBase, q
from tests.test_discovery import job


class Base(FilterBase):
    def setUp(self):
        super().setUp()
        self.store = self.app.state.store
        self.add(role='Python SQL Developer', employer='Acme', description='Python and SQL', department=None, industry=None,
                 original_url='https://example.invalid/jobs/acme')
        self.add(role='Rust Developer', employer='Beta', description='Rust and Golang', department=None, industry=None)
        self.verified_resume(('skills', 'Python SQL secret-resume-phrase'), ('employment', 'Software developer intern'))
        self.preset = self.store.add_preset('Devs', jobfilters.dump_preferences(jobfilters.parse(q('min_score=40'))))

    def digests(self, **kw):
        return notify.build_digests(self.jobdb, self.store, **kw)


class StoreTests(Base):
    def test_preset_rules(self):
        with self.assertRaises(ValueError): self.store.add_preset('Devs', '{}')           # unique name
        with self.assertRaises(ValueError): self.store.add_preset('  ', '{}')
        for i in range(9): self.store.add_preset(f'P{i}', '{}')
        with self.assertRaises(ValueError): self.store.add_preset('Too many', '{}')
        self.store.mark_sent(self.preset, [1, 2])
        self.store.delete_preset(self.preset)
        self.assertEqual(len(self.store.presets()), 9); self.assertEqual(self.store.sent_ids(self.preset), set())


class DigestTests(Base):
    def test_digest_has_top_matches_only_and_each_posting_is_sent_once(self):
        first = self.digests()[0]
        self.assertEqual([i['row'].job.employer for i in first.items], ['Acme'])      # Rust job is below the 40% preset minimum
        sent = []
        report = notify.send_digests(self.store, [first], lambda *a: sent.append(a))
        self.assertEqual((report['emails'], report['postings']), (1, 1))
        self.assertEqual(self.digests()[0].items, [])                                  # nothing new: no second email
        self.add(role='Python Engineer', employer='Gamma', description='Python SQL', department=None, industry=None)
        again = self.digests()[0]
        self.assertEqual([i['row'].job.employer for i in again.items], ['Gamma'])      # only the new posting

    def test_failed_send_does_not_mark_postings_sent(self):
        def broken(*a): raise OSError('network down')
        report = notify.send_digests(self.store, self.digests(), broken)
        self.assertEqual(report['emails'], 0); self.assertTrue(report['errors'])
        self.assertEqual(len(self.digests()[0].items), 1)

    def test_paused_preset_dismissed_job_and_missing_resume(self):
        self.store.set_preset_enabled(self.preset, False)
        self.assertEqual(self.digests(), [])
        self.store.set_preset_enabled(self.preset, True)
        first = self.digests()[0]; self.store.dismiss(first.items[0]['row'].id)
        self.assertEqual(self.digests()[0].items, [])
        self.store.dismiss(first.items[0]['row'].id, hide=False)
        for fact in self.store.facts(1):
            self.store.fact(1, fact['category'], fact['value'], 'pending', fact['id'])
        digest = self.digests()[0]
        self.assertEqual(digest.items, []); self.assertTrue(any('verified' in n for n in digest.notes))

    def test_email_contains_postings_and_scores_but_no_resume_text_and_escapes_job_text(self):
        self.add(role='<script>alert(1)</script> Python SQL', employer='<b>Evil</b>', description='Python SQL', department=None, industry=None,
                 original_url='javascript:alert(1)', application_url='ftp://example.invalid/x')
        digest = self.digests()[0]
        subject, text, html = notify.render(digest)
        self.assertNotIn('secret-resume-phrase', text + html + subject)
        self.assertNotIn('<script>', html); self.assertIn('&lt;script&gt;', html)
        self.assertNotIn('javascript:', html + text); self.assertNotIn('ftp:', html + text)
        self.assertIn('https://example.invalid/jobs/acme', html); self.assertIn('verify each is still open', text)
        self.assertRegex(text, r'\d+%')

    def test_digest_size_is_bounded_and_rolls_over(self):
        for i in range(5): self.add(role=f'Python SQL Dev {i}', employer=f'Co{i}', description='Python SQL', department=None, industry=None)
        small = notify.build_digests(self.jobdb, self.store, limit=2)[0]
        self.assertEqual(len(small.items), 2); self.assertGreaterEqual(small.total_new, 6)
        self.assertIn('more matching', notify.render(small)[1])


class SenderTests(unittest.TestCase):
    def test_gmail_sender_only_mails_the_owner_and_uses_keychain_password(self):
        server = MagicMock(); smtp = MagicMock(); smtp.return_value.__enter__.return_value = server
        send = notify.gmail_sender('me@gmail.com', lambda address: 'app-password', smtp)
        send('Hello\r\nBcc: x@evil.com', 'body', '<p>body</p>')
        server.login.assert_called_once_with('me@gmail.com', 'app-password')
        message = server.send_message.call_args[0][0]
        self.assertEqual((message['From'], message['To']), ('me@gmail.com', 'me@gmail.com'))
        self.assertNotIn('\n', message['Subject']); self.assertIsNone(message['Bcc'])
        smtp.assert_called_with('smtp.gmail.com', 465, timeout=30)
        for bad in ('me@example.com', 'a@b.com', 'x y@gmail.com', '', 'YOU@gmail.com', 'your_name@gmail.com'):
            with self.assertRaises(ValueError): notify.gmail_sender(bad)

    def test_missing_keychain_entry_gives_setup_instructions(self):
        runner = MagicMock(return_value=MagicMock(returncode=44, stdout=''))
        with self.assertRaises(RuntimeError) as caught: notify.keychain_password('me@gmail.com', runner)
        self.assertIn('notify setup', str(caught.exception))
        runner.return_value = MagicMock(returncode=0, stdout='pw\n')
        self.assertEqual(notify.keychain_password('me@gmail.com', runner), 'pw')
        runner.return_value = MagicMock(returncode=0, stdout='abcd efgh ijkl mnop\n')
        self.assertEqual(notify.keychain_password('me@gmail.com', runner), 'abcdefghijklmnop')
        self.assertNotIn('pw', ' '.join(runner.call_args[0][0]))


class CliTests(Base):
    def run_cli(self, action, factory=None, **extra):
        args = argparse.Namespace(notify_action=action, preset=None, **extra)
        out = io.StringIO()
        settings = MagicMock(database=self.jobdb)
        with redirect_stdout(out), patch.object(notify_cli, 'PRIVATE', self.root / 'private'):
            code = notify_cli.run(args, settings, factory or MagicMock(), self.store)
        return code, out.getvalue()

    def test_preview_writes_files_and_sends_nothing(self):
        with patch.object(notify_cli, 'PRIVATE', self.root / 'private'):
            code, out = self.run_cli('preview')
        self.assertEqual(code, 0); self.assertIn('Nothing was sent', out)
        self.assertTrue(list((self.root / 'private' / 'digests').glob('*.html')))
        self.assertEqual(self.store.sent_ids(self.preset), set())

    def test_send_requires_setup_then_sends_and_marks(self):
        with self.assertRaises(ConfigurationError): self.run_cli('send')
        self.store.set_setting('notify_address', 'me@gmail.com')
        sender = MagicMock(); factory = MagicMock(return_value=sender)
        code, out = self.run_cli('send', factory)
        self.assertEqual(code, 0); sender.assert_called_once(); factory.assert_called_with('me@gmail.com')
        self.assertEqual(len(self.store.sent_ids(self.preset)), 1)
        code, out = self.run_cli('send', factory); self.assertEqual(sender.call_count, 1)   # nothing new, no second email

    def test_after_search_never_raises(self):
        out = io.StringIO()
        with redirect_stdout(out):
            notify_cli.after_search(MagicMock(database=self.jobdb), store=self.store)                     # not set up
            self.store.set_setting('notify_address', 'me@gmail.com')
            notify_cli.after_search(MagicMock(database=self.jobdb), MagicMock(side_effect=RuntimeError('x')), self.store)
        self.assertIn('not set up', out.getvalue()); self.assertIn('skipped', out.getvalue())

    def test_setup_rejects_non_gmail_and_placeholder_addresses(self):
        for address in ('me@example.com', 'YOU@gmail.com', 'YOUR_NAME@gmail.com'):
            with self.assertRaises(ConfigurationError): self.run_cli('setup', address=address)

    def test_test_command_explains_disconnects_without_leaking_secrets(self):
        import smtplib
        self.store.set_setting('notify_address', 'jane.smith@gmail.com')
        with self.assertRaises(ConfigurationError) as caught:
            self.run_cli('test', MagicMock(return_value=MagicMock(side_effect=smtplib.SMTPServerDisconnected('x'))))
        self.assertIn('wrong saved address', str(caught.exception)); self.assertIn('ja***@gmail.com', str(caught.exception))
        self.assertNotIn('jane.smith', str(caught.exception))


class DashboardTests(Base):
    def setUp(self):
        super().setUp(); self.login()

    def post(self, path, **data):
        return self.client.post(path, data={'csrf': self.csrf('/notifications'), **data}, follow_redirects=False)

    def test_create_toggle_delete_preset_from_current_filters(self):
        page = self.client.get('/jobs?applied=1&field=Technology&min_score=50')
        self.assertIn('Save as email preset', page.text)
        blob = re.search(r'name="filters" value="([^"]+)"', page.text)[1]
        import html; blob = html.unescape(blob)
        created = self.post('/notifications/create', name='My preset', filters=blob)
        self.assertEqual(created.status_code, 303)
        listing = self.client.get('/notifications').text
        self.assertIn('My preset', listing); self.assertIn('Field: Technology', listing); self.assertIn('Match ≥ 50%', listing)
        self.assertIn('notify setup', listing)
        pid = [p for p in self.store.presets() if p['name'] == 'My preset'][0]['id']
        self.post('/notifications/toggle', preset_id=str(pid), enabled='0'); self.assertFalse([p for p in self.store.presets() if p['id'] == pid][0]['enabled'])
        self.post('/notifications/delete', preset_id=str(pid)); self.assertFalse([p for p in self.store.presets() if p['id'] == pid])

    def test_bad_input_shows_message_and_csrf_is_required(self):
        bad = self.post('/notifications/create', name='x', filters='not json')
        self.assertEqual(bad.status_code, 400); self.assertIn('could not be read', bad.text)
        dup = self.post('/notifications/create', name='Devs', filters=jobfilters.dump_preferences(jobfilters.JobFilters()))
        self.assertEqual(dup.status_code, 400); self.assertIn('already exists', dup.text)
        for path in ('/notifications/create', '/notifications/toggle', '/notifications/delete'):
            self.assertEqual(self.client.post(path, data={'preset_id': '1'}).status_code, 403)

    def test_no_send_route_exists_in_the_web_interface(self):
        self.assertEqual(self.client.post('/notifications/send', data={'csrf': self.csrf()}).status_code, 404)


if __name__ == '__main__':
    unittest.main()
