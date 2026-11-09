import copy
import imaplib
import sqlite3
import tempfile
import unittest
from contextlib import closing
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

from source_mail import DEFAULT_MAIL, MAX_BYTES, canonical_link, parse_message, read_mail, validate_settings


def message(sender="Avito <notify@avito.ru>", eid=123456, html=True):
    msg = EmailMessage()
    msg["From"] = sender
    msg["Subject"] = "Новые объявления: RTX 3070"
    msg.set_content(f"https://www.avito.ru/moskva/tovary/gpu_{eid}?utm=mail")
    if html:
        msg.add_alternative(f'<a href="https://www.avito.ru/moskva/tovary/gpu_{eid}?a=1&amp;b=2">RTX 3070</a>', subtype="html")
    return msg.as_bytes()


class FakeIMAP:
    def __init__(self):
        self.messages = {1: message()}
        self.validity = b"1"
        self.failed = None
        self.large = set()
        self.commands = []
        self.logged_out = False

    def __enter__(self): return self
    def __exit__(self, *args): self.logged_out = True
    def login(self, *args): pass
    def select(self, folder, readonly=False):
        assert readonly
        self.folder = folder
        return "OK", [b"1"]
    def response(self, name): return name, [self.validity]
    def uid(self, command, *args):
        self.commands.append((command, args))
        if command == "search":
            return "OK", [b" ".join(str(x).encode() for x in self.messages)]
        uid = int(args[0])
        if args[1] == "(RFC822.SIZE)":
            size = MAX_BYTES+1 if uid in self.large else len(self.messages[uid])
            return "OK", [f"{uid} (RFC822.SIZE {size})".encode()]
        assert args[1] == "(BODY.PEEK[])"
        if uid == self.failed:
            return "NO", [b"connection failure"]
        return "OK", [(b"literal", self.messages[uid]), b")"]


class MailTests(unittest.TestCase):
    def test_multipart_decoding_and_canonical_dedup(self):
        items = parse_message(message())
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "RTX 3070")
        self.assertNotIn("?", items[0]["url"])
        self.assertNotIn("price", items[0])

    def test_sender_and_link_boundaries(self):
        for sender in ("avito.ru@evil.test", "a@notavito.ru", "a@avito.ru.evil.test", "a@avito.ru, b@evil.test"):
            self.assertEqual(parse_message(message(sender)), [])
        for link in ("http://avito.ru/x_123456", "https://avito.ru.evil.test/x_123456",
                     "https://user@avito.ru/x_123456", "https://avito.ru:444/x_123456",
                     "https://avito.ru/search?q=123456", "https://click.avito.ru/redirect?url=x_123456"):
            self.assertIsNone(canonical_link(link), link)

    def test_plain_and_attachments(self):
        self.assertEqual(len(parse_message(message(html=False))), 1)
        msg = EmailMessage()
        msg["From"] = "notify@avito.ru"
        msg.set_content("Нет прямой ссылки")
        msg.add_attachment(message(), maintype="message", subtype="rfc822")
        self.assertEqual(parse_message(msg.as_bytes()), [])

    def test_settings_validation(self):
        self.assertFalse(DEFAULT_MAIL["enabled"])
        for key, bad in (("host", "https://mail.test"), ("port", 0), ("folder", "a\nLOGOUT"),
                         ("interval_minutes", 0), ("lookback_days", 100)):
            with self.assertRaises(ValueError):
                validate_settings(dict(DEFAULT_MAIL, username="a@yandex.ru", **{key: bad}))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = str(Path(self.temp.name)/"mail.sqlite3")
        self.settings = dict(DEFAULT_MAIL, username="user@yandex.ru")
        self.server = FakeIMAP()
        mock = patch("source_mail.imaplib.IMAP4_SSL", return_value=self.server)
        self.factory = mock.start()
        self.addCleanup(mock.stop)

    def read(self, **kwargs): return read_mail(self.settings, "secret-test", self.db, **kwargs)

    def test_readonly_tls_restart_and_duplicate(self):
        self.assertEqual(self.read()["new"], 1)
        self.assertEqual(self.read()["processed"], 0)
        self.server.messages[2] = message()
        self.assertEqual(self.read()["new"], 0)
        self.assertEqual(self.server.folder, '"INBOX"')
        self.assertTrue(self.server.logged_out)
        self.assertTrue(self.factory.call_args.kwargs["ssl_context"].check_hostname)
        self.assertEqual(self.factory.call_args.kwargs["timeout"], 30)
        search_args = [args for cmd,args in self.server.commands if cmd == "search"]
        self.assertIn("SINCE", search_args[0])
        self.assertIn("UID", search_args[1])
        self.assertIn("FROM", search_args[1])

    def test_failed_fetch_keeps_cursor_and_recovers(self):
        self.server.messages[2] = message(eid=234567)
        self.server.failed = 2
        with self.assertRaises(RuntimeError): self.read()
        with closing(sqlite3.connect(self.db)) as db:
            self.assertEqual(db.execute("SELECT uid FROM mail_cursor").fetchone()[0], 1)
        self.server.failed = None
        self.assertEqual(self.read()["new"], 1)

    def test_uidvalidity_reset_and_account_isolation(self):
        self.read()
        self.server.validity = b"2"
        self.server.messages = {1: message(eid=234567)}
        self.assertEqual(self.read()["new"], 1)
        self.settings["folder"] = "Avito"
        self.assertEqual(self.read()["processed"], 1)
        self.assertEqual(self.read()["processed"], 0)

    def test_batch_does_not_skip_and_oversize_visible(self):
        self.server.messages = {i: message(eid=100000+i) for i in range(1, 54)}
        self.server.large = {1}
        first = self.read()
        self.assertEqual((first["processed"], first["pending"], first["oversized"]), (50, 3, 1))
        self.assertEqual(self.read()["new"], 3)

    def test_connection_test_does_not_read_or_store(self):
        self.assertIn("message", self.read(check_only=True))
        self.assertFalse(self.server.commands)
        self.assertFalse(Path(self.db).exists())

    def test_login_error_does_not_expose_secret(self):
        def fail(*args): raise imaplib.IMAP4.error("secret-test user@yandex.ru")
        self.server.login = fail
        with self.assertRaises(RuntimeError) as caught: self.read()
        self.assertNotIn("secret-test", str(caught.exception))
        self.assertNotIn("user@yandex.ru", str(caught.exception))


if __name__ == "__main__": unittest.main()
