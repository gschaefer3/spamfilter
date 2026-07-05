import email
import imaplib
import logging
import os
import re
import socket
import ssl
from dataclasses import dataclass
from email.header import decode_header
from typing import Optional

import yaml

logger = logging.getLogger("spamfilter")


@dataclass
class Account:
    name: str
    imap_server: str
    imap_port: int
    smtp_server: str
    smtp_port: int
    username: str
    password: str
    inbox_folder: str = "INBOX"
    spam_folder: str = "Junk"
    create_spam_folder: bool = True


@dataclass
class RunResult:
    account: str
    evaluated: int = 0
    ham: int = 0
    spam: int = 0
    moved: int = 0
    error: str = ""


@dataclass
class Settings:
    scanned_keyword: str = "SpamFilterScanned"
    max_messages_per_account: int = 50
    ollama_url: str = "http://localhost:11434/api/generate"
    ollama_model: str = "llama3"
    ollama_timeout_seconds: int = 30


def load_config(config_path: str) -> tuple[list[Account], Settings]:
    with open(config_path, "r") as f:
        raw = yaml.safe_load(f)

    accounts = []
    for entry in raw.get("accounts", []):
        password_env = entry.get("password_env")
        password = os.environ.get(password_env, "") if password_env else entry.get("password", "")
        if not password:
            raise ValueError(
                f"No password found for account '{entry.get('name')}'. "
                f"Set the environment variable '{password_env}'."
            )
        accounts.append(
            Account(
                name=entry["name"],
                imap_server=entry["imap_server"],
                imap_port=int(entry.get("imap_port", 993)),
                smtp_server=entry.get("smtp_server", ""),
                smtp_port=int(entry.get("smtp_port", 587)),
                username=entry["username"],
                password=password,
                inbox_folder=entry.get("inbox_folder", "INBOX"),
                spam_folder=entry.get("spam_folder", "Junk"),
                create_spam_folder=bool(entry.get("create_spam_folder", True)),
            )
        )

    ollama_cfg = raw.get("ollama", {})
    settings_cfg = raw.get("settings", {})
    settings = Settings(
        scanned_keyword=settings_cfg.get("scanned_keyword", "SpamFilterScanned"),
        max_messages_per_account=int(settings_cfg.get("max_messages_per_account", 50)),
        ollama_url=ollama_cfg.get("url", "http://localhost:11434/api/generate"),
        ollama_model=ollama_cfg.get("model", "llama3"),
        ollama_timeout_seconds=int(ollama_cfg.get("timeout_seconds", 30)),
    )

    return accounts, settings


def _decode_mime_header(value: Optional[str]) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    decoded = []
    for text, charset in parts:
        if isinstance(text, bytes):
            decoded.append(text.decode(charset or "utf-8", errors="ignore"))
        else:
            decoded.append(text)
    return "".join(decoded)


def _strip_html(html: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def extract_body(msg: email.message.Message) -> str:
    plain_parts = []
    html_parts = []

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition") or "")
            if "attachment" in disposition:
                continue
            charset = part.get_content_charset() or "utf-8"
            try:
                payload = part.get_payload(decode=True)
            except Exception:
                continue
            if payload is None:
                continue
            text = payload.decode(charset, errors="ignore")
            if content_type == "text/plain":
                plain_parts.append(text)
            elif content_type == "text/html":
                html_parts.append(text)
    else:
        charset = msg.get_content_charset() or "utf-8"
        payload = msg.get_payload(decode=True)
        if payload is not None:
            text = payload.decode(charset, errors="ignore")
            if msg.get_content_type() == "text/html":
                html_parts.append(text)
            else:
                plain_parts.append(text)

    if plain_parts:
        return "\n".join(plain_parts)
    if html_parts:
        return _strip_html("\n".join(html_parts))
    return ""


def _quote_folder(folder: str) -> str:
    return f'"{folder}"'


def _is_timeout_error(exc: Exception) -> bool:
    return isinstance(exc, (socket.timeout, TimeoutError, ssl.SSLError))


def process_account(account: Account, settings: Settings, classify_fn, dry_run: bool) -> RunResult:
    result = RunResult(account=account.name)
    logger.info("[%s] connecting to %s:%s", account.name, account.imap_server, account.imap_port)
    mail = imaplib.IMAP4_SSL(account.imap_server, account.imap_port, timeout=60)
    try:
        try:
            mail.login(account.username, account.password)
        except (socket.timeout, TimeoutError, ssl.SSLError) as exc:
            result.error = f"login timed out: {exc}"
            logger.error("[%s] %s", account.name, result.error)
            return result

        capabilities = mail.capabilities
        supports_move = b"MOVE" in capabilities

        if account.create_spam_folder:
            try:
                mail.create(_quote_folder(account.spam_folder))
            except Exception as exc:
                logger.info("[%s] spam folder '%s' already exists or could not be created: %s", account.name, account.spam_folder, exc)

        try:
            status, _ = mail.select(_quote_folder(account.inbox_folder))
        except (socket.timeout, TimeoutError, ssl.SSLError) as exc:
            result.error = f"select folder timed out: {exc}"
            logger.error("[%s] %s", account.name, result.error)
            return result

        if status != "OK":
            result.error = f"could not select folder '{account.inbox_folder}'"
            logger.error("[%s] %s", account.name, result.error)
            return result

        criteria = f"(UNSEEN NOT KEYWORD {settings.scanned_keyword})"
        try:
            status, data = mail.uid("search", None, criteria)
        except (socket.timeout, TimeoutError, ssl.SSLError) as exc:
            result.error = f"search timed out: {exc}"
            logger.error("[%s] %s", account.name, result.error)
            return result

        if status != "OK":
            result.error = "search failed"
            logger.error("[%s] %s", account.name, result.error)
            return result

        uids = data[0].split()
        if settings.max_messages_per_account:
            uids = uids[: settings.max_messages_per_account]

        logger.info("[%s] %d message(s) to evaluate", account.name, len(uids))

        for uid in uids:
            try:
                status, msg_data = mail.uid("fetch", uid, "(BODY.PEEK[])")
            except (socket.timeout, TimeoutError, ssl.SSLError) as exc:
                logger.warning("[%s] failed to fetch uid %s due to timeout: %s", account.name, uid.decode(), exc)
                continue

            if status != "OK" or not msg_data or msg_data[0] is None:
                logger.warning("[%s] failed to fetch uid %s", account.name, uid)
                continue

            raw_bytes = msg_data[0][1]
            msg = email.message_from_bytes(raw_bytes)
            subject = _decode_mime_header(msg.get("Subject"))
            sender = _decode_mime_header(msg.get("From"))
            body = extract_body(msg)

            is_spam, reason = classify_fn(body)
            verdict = "SPAM" if is_spam else "HAM"
            logger.info("[%s] uid=%s from=%r subject=%r -> %s (%s)", account.name, uid.decode(), sender, subject, verdict, reason)

            result.evaluated += 1
            if is_spam:
                result.spam += 1
            else:
                result.ham += 1

            if dry_run:
                continue

            try:
                status, resp = mail.uid("store", uid, "+FLAGS", f"({settings.scanned_keyword})")
            except (socket.timeout, TimeoutError, ssl.SSLError) as exc:
                logger.warning("[%s] uid=%s failed to set scanned flag due to timeout: %s", account.name, uid.decode(), exc)
                continue

            if status != "OK":
                logger.warning("[%s] uid=%s failed to set scanned flag: %s", account.name, uid.decode(), resp)

            if is_spam:
                if supports_move:
                    try:
                        status, resp = mail.uid("MOVE", uid, _quote_folder(account.spam_folder))
                    except (socket.timeout, TimeoutError, ssl.SSLError) as exc:
                        logger.error("[%s] uid=%s MOVE to '%s' timed out: %s", account.name, uid.decode(), account.spam_folder, exc)
                        continue
                    if status != "OK":
                        logger.error("[%s] uid=%s MOVE to '%s' failed: %s", account.name, uid.decode(), account.spam_folder, resp)
                        continue
                else:
                    try:
                        status, resp = mail.uid("copy", uid, _quote_folder(account.spam_folder))
                    except (socket.timeout, TimeoutError, ssl.SSLError) as exc:
                        logger.error("[%s] uid=%s COPY to '%s' timed out: %s", account.name, uid.decode(), account.spam_folder, exc)
                        continue
                    if status != "OK":
                        logger.error("[%s] uid=%s COPY to '%s' failed: %s", account.name, uid.decode(), account.spam_folder, resp)
                        continue
                    try:
                        status, resp = mail.uid("store", uid, "+FLAGS", "(\\Deleted)")
                    except (socket.timeout, TimeoutError, ssl.SSLError) as exc:
                        logger.error("[%s] uid=%s STORE \\Deleted timed out: %s", account.name, uid.decode(), exc)
                        continue
                    if status != "OK":
                        logger.error("[%s] uid=%s STORE \\Deleted failed: %s", account.name, uid.decode(), resp)
                        continue
                    try:
                        status, resp = mail.expunge()
                    except (socket.timeout, TimeoutError, ssl.SSLError) as exc:
                        logger.error("[%s] uid=%s EXPUNGE timed out: %s", account.name, uid.decode(), exc)
                        continue
                    if status != "OK":
                        logger.error("[%s] uid=%s EXPUNGE failed: %s", account.name, uid.decode(), resp)
                        continue

                result.moved += 1
                if supports_move:
                    try:
                        status, resp = mail.uid("MOVE", uid, _quote_folder(account.spam_folder))
                    except (socket.timeout, TimeoutError, ssl.SSLError) as exc:
                        logger.error("[%s] uid=%s MOVE to '%s' timed out: %s", account.name, uid.decode(), account.spam_folder, exc)
                        continue
                    if status != "OK":
                        logger.error("[%s] uid=%s MOVE to '%s' failed: %s", account.name, uid.decode(), account.spam_folder, resp)
                        continue
                else:
                    try:
                        status, resp = mail.uid("copy", uid, _quote_folder(account.spam_folder))
                    except (socket.timeout, TimeoutError, ssl.SSLError) as exc:
                        logger.error("[%s] uid=%s COPY to '%s' timed out: %s", account.name, uid.decode(), account.spam_folder, exc)
                        continue
                    if status != "OK":
                        logger.error("[%s] uid=%s COPY to '%s' failed: %s", account.name, uid.decode(), account.spam_folder, resp)
                        continue
                    try:
                        status, resp = mail.uid("store", uid, "+FLAGS", "(\\Deleted)")
                    except (socket.timeout, TimeoutError, ssl.SSLError) as exc:
                        logger.error("[%s] uid=%s STORE \\Deleted timed out: %s", account.name, uid.decode(), exc)
                        continue
                    if status != "OK":
                        logger.error("[%s] uid=%s STORE \\Deleted failed: %s", account.name, uid.decode(), resp)
                        continue
                    try:
                        status, resp = mail.expunge()
                    except (socket.timeout, TimeoutError, ssl.SSLError) as exc:
                        logger.error("[%s] uid=%s EXPUNGE timed out: %s", account.name, uid.decode(), exc)
                        continue
                    if status != "OK":
                        logger.error("[%s] uid=%s EXPUNGE failed: %s", account.name, uid.decode(), resp)
                        continue
                result.moved += 1
    finally:
        try:
            mail.logout()
        except Exception:
            pass

    return result
