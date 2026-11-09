from contextlib import closing
"""Read the user's own notification mailbox. Never requests Avito pages."""
import hashlib
import imaplib
import re
import sqlite3
import ssl
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses
from html.parser import HTMLParser
from urllib.parse import urlsplit, urlunsplit

DEFAULT_MAIL = {"enabled": False, "host": "imap.yandex.ru", "port": 993,
                "username": "", "folder": "INBOX", "interval_minutes": 5,
                "lookback_days": 7}
MAX_BYTES = 2 * 1024 * 1024
BATCH = 50


def validate_settings(settings):
    value = dict(DEFAULT_MAIL, **settings)
    if not re.fullmatch(r"[a-zA-Z0-9.-]+", value["host"].strip()):
        raise ValueError("Укажите имя IMAP-сервера без https://.")
    value["host"] = value["host"].strip().lower()
    value["username"] = value["username"].strip()
    if not value["username"] or "@" not in value["username"]:
        raise ValueError("Укажите полный адрес электронной почты.")
    for key, low, high in (("port", 1, 65535), ("interval_minutes", 1, 1440),
                           ("lookback_days", 1, 30)):
        value[key] = int(value[key])
        if not low <= value[key] <= high:
            raise ValueError(f"{key}: допустимо от {low} до {high}.")
    # INBOX or an ASCII folder name; no ambiguous IMAP UTF-7 conversion.
    if not value["folder"] or any(ord(c) < 32 or ord(c) > 126 for c in value["folder"]):
        raise ValueError("Используйте INBOX или папку с английским названием, например Avito.")
    return value


def canonical_link(link):
    try:
        uri = urlsplit(link.strip())
        if (uri.scheme != "https" or uri.hostname not in {"avito.ru", "www.avito.ru", "m.avito.ru"}
                or uri.username or uri.password or uri.port not in (None, 443)):
            return None
        match = re.search(r"_([0-9]{5,})/?$", uri.path)
        if not match:
            return None
        return match[1], urlunsplit(("https", "www.avito.ru", uri.path.rstrip("/"), "", ""))
    except ValueError:
        return None


class Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.current = None
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.current = dict(attrs).get("href", "")
            self.parts = []

    def handle_data(self, data):
        if self.current is not None:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self.current is not None:
            self.links.append((self.current, " ".join(self.parts)))
            self.current = None


def parse_message(raw):
    """Only direct item links; no redirect unwrapping, HTML rendering or price guesses.

    From is a convenience filter, NOT cryptographic sender authentication.
    """
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    senders = getaddresses(msg.get_all("From", []))
    if len(senders) != 1:
        return []
    domain = senders[0][1].rpartition("@")[2].lower()
    if domain != "avito.ru" and not domain.endswith(".avito.ru"):
        return []
    links = []
    def body_parts(part):
        if part.get_content_disposition() == "attachment" or part.get_content_type() == "message/rfc822":
            return
        if part.is_multipart():
            for child in part.iter_parts():
                yield from body_parts(child)
        else:
            yield part

    for part in body_parts(msg):
        if part.get_content_type() not in ("text/plain", "text/html"):
            continue
        try:
            content = part.get_content()
        except (LookupError, UnicodeError):
            content = (part.get_payload(decode=True) or b"").decode("utf-8", errors="replace")
        if part.get_content_type() == "text/html":
            parser = Links()
            parser.feed(content)
            links.extend(parser.links)
        else:
            links.extend((x.rstrip(".,;)>]"), "") for x in re.findall(r'https://[^\s<>"\']+', content))
    items = {}
    for link, title in links:
        canonical = canonical_link(link)
        if canonical:
            eid, url = canonical
            clean_title = " ".join(title.split())[:300]
            item = {"external_id": eid, "url": url,
                    "title": clean_title or str(msg.get("Subject", "Объявление Авито"))[:300],
                    "subject": str(msg.get("Subject", ""))[:500],
                    "mail_date": str(msg.get("Date", ""))[:100]}
            if eid not in items or clean_title:
                items[eid] = item
    return list(items.values())


def init_mail_db(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS mail_cursor(mailbox TEXT PRIMARY KEY, validity TEXT, uid INTEGER);
    CREATE TABLE IF NOT EXISTS mail_items(external_id TEXT PRIMARY KEY, title TEXT, url TEXT,
        subject TEXT, mail_date TEXT, received_at TEXT);
    """)


def _ok(status):
    if status != "OK":
        raise RuntimeError("Почтовый сервер не выполнил команду. Проверьте папку и доступ IMAP.")


def read_mail(settings, password, db_path, check_only=False):
    settings = validate_settings(settings)
    if not password:
        raise ValueError("Укажите пароль приложения почты.")
    mailbox = hashlib.sha256(repr((settings["host"], settings["port"], settings["username"],
                                 settings["folder"])).encode()).hexdigest()
    result = {"processed": 0, "new": 0, "without_links": 0, "oversized": 0, "pending": 0}
    try:
        with imaplib.IMAP4_SSL(settings["host"], settings["port"],
                              ssl_context=ssl.create_default_context(), timeout=30) as client:
            client.login(settings["username"], password)
            folder = '"' + settings["folder"].replace("\\", "\\\\").replace('"', '\\"') + '"'
            status, _ = client.select(folder, readonly=True)
            _ok(status)
            if check_only:
                return {"message": "Подключение успешно. Папка доступна только для чтения."}
            _, validity_data = client.response("UIDVALIDITY")
            if not validity_data or not validity_data[0]:
                raise RuntimeError("Сервер не сообщил UIDVALIDITY; безопасное продолжение невозможно.")
            validity = validity_data[0].decode("ascii")
            with closing(sqlite3.connect(db_path, timeout=30)) as db:
                init_mail_db(db)
                row = db.execute("SELECT validity,uid FROM mail_cursor WHERE mailbox=?", (mailbox,)).fetchone()
                cursor = row[1] if row and row[0] == validity else 0
                # Fixed English month names: IMAP dates must not depend on Windows locale.
                since = datetime.now(timezone.utc) - timedelta(days=settings["lookback_days"])
                months = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
                date = f"{since.day:02d}-{months[since.month-1]}-{since.year}"
                criteria = (("UID", f"{cursor+1}:*") if cursor else ("SINCE", date)) + ("FROM", '"avito.ru"')
                status, data = client.uid("search", None, *criteria)
                _ok(status)
                uids = sorted({int(x) for x in data[0].split() if int(x) > cursor})
                result["pending"] = max(0, len(uids) - BATCH)
                for uid in uids[:BATCH]:
                    status, metadata = client.uid("fetch", str(uid), "(RFC822.SIZE)")
                    _ok(status)
                    sizes = re.findall(rb"RFC822.SIZE (\d+)", b" ".join(x for x in metadata if isinstance(x, bytes)))
                    if not sizes:
                        raise RuntimeError("Не удалось проверить размер письма; повторим при следующей проверке.")
                    if int(sizes[0]) > MAX_BYTES:
                        result["oversized"] += 1
                    else:
                        status, data = client.uid("fetch", str(uid), "(BODY.PEEK[])")
                        _ok(status)
                        raw = next((x[1] for x in data if isinstance(x, tuple)), None)
                        if raw is None or len(raw) > MAX_BYTES:
                            raise RuntimeError("Письмо не загружено полностью; повторим позже.")
                        items = parse_message(raw)
                        if not items:
                            result["without_links"] += 1
                        for item in items:
                            added = db.execute("INSERT OR IGNORE INTO mail_items VALUES(?,?,?,?,?,?)",
                                (item["external_id"], item["title"], item["url"], item["subject"],
                                 item["mail_date"], datetime.now(timezone.utc).isoformat())).rowcount
                            result["new"] += added
                    # Items and cursor commit together; failed fetch cannot lose an email.
                    db.execute("INSERT OR REPLACE INTO mail_cursor VALUES(?,?,?)", (mailbox, validity, uid))
                    db.commit()
                    result["processed"] += 1
    except imaplib.IMAP4.error as exc:
        # Server messages may contain account details; never surface them or a password.
        raise RuntimeError("Ошибка IMAP. Проверьте адрес, пароль приложения и разрешение доступа к почте.") from None
    except (OSError, ssl.SSLError):
        raise RuntimeError("Не удалось безопасно подключиться к почте. Проверьте сервер, порт и интернет.") from None
    return result
