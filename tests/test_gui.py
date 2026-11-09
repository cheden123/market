"""Windows GUI settings checks, using isolated files and a fake credential store."""
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import app_gui


@unittest.skipUnless(sys.platform == "win32", "Windows desktop GUI")
class GUISettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        directory = Path(self.temp.name)
        self.passwords = {}
        fake_keyring = types.SimpleNamespace(
            get_password=lambda service, key: self.passwords.get(key),
            set_password=lambda service, key, value: self.passwords.__setitem__(key, value),
            delete_password=lambda service, key: self.passwords.pop(key, None),
            errors=types.SimpleNamespace(PasswordDeleteError=RuntimeError))
        for mock in (patch.dict(sys.modules, {"keyring": fake_keyring}),
                     patch.object(app_gui, "APP_DIR", directory),
                     patch.object(app_gui, "CONFIG", directory / "config.json"),
                     patch.object(app_gui.messagebox, "showinfo"),
                     patch.object(app_gui.messagebox, "showerror")):
            mock.start()
            self.addCleanup(mock.stop)
        self.app = app_gui.Application()
        self.app.withdraw()
        self.addCleanup(self.app.close_app)

    def test_mail_save_secret_separation_and_timer(self):
        self.app.cfg["monitor"] = {"enabled": True}
        self.app.mail_vars["username"].set("test@yandex.ru")
        self.app.mail_password.set("test-app-password")
        self.app.mail_enabled.set(True)
        self.app.save_mail_settings()
        self.assertIsNotNone(self.app.mail_timer)
        self.assertEqual(self.passwords["imap_password"], "test-app-password")
        stored = app_gui.CONFIG.read_text(encoding="utf-8")
        self.assertNotIn("test-app-password", stored)
        cfg = json.loads(stored)
        self.assertTrue(cfg["mail"]["enabled"])
        self.assertFalse(cfg["monitor"]["enabled"])
        self.app.mail_enabled.set(False)
        self.app.mail_password.set("")
        self.app.save_mail_settings()
        self.assertIsNone(self.app.mail_timer)
        self.assertNotIn("imap_password", self.passwords)

    def test_invalid_settings_do_not_save(self):
        self.app.mail_vars["username"].set("test@yandex.ru")
        self.app.mail_enabled.set(True)
        self.app.save_mail_settings()
        self.assertFalse(app_gui.CONFIG.exists())
        self.assertFalse(self.passwords)
        app_gui.messagebox.showerror.assert_called_once()


if __name__ == "__main__": unittest.main()
