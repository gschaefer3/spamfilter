import socket
import ssl
from unittest.mock import Mock, patch

import pytest

from mail_client import process_account


class DummyAccount:
    name = "test"
    imap_server = "imap.example.com"
    imap_port = 993
    smtp_server = "smtp.example.com"
    smtp_port = 587
    username = "user"
    password = "pass"
    inbox_folder = "INBOX"
    spam_folder = "Junk"
    create_spam_folder = True


@pytest.fixture
def account():
    return DummyAccount()


def test_process_account_recovers_from_timeout_during_store(account):
    mail = Mock()
    mail.capabilities = b"MOVE"
    mail.select.return_value = ("OK", None)
    mail.uid.side_effect = [
        ("OK", [b"1"]),
        ("OK", [(b"1", b"Subject: hi\n\nhello")]),
        socket.timeout("timed out"),
    ]
    mail.logout.return_value = ("BYE", None)

    with patch("mail_client.imaplib.IMAP4_SSL", return_value=mail), patch("mail_client.email.message_from_bytes") as message_from_bytes:
        message = Mock()
        message.get.return_value = "Subject"
        message.get_content_type.return_value = "text/plain"
        message.get_payload.return_value = b"hello"
        message.get_content_charset.return_value = None
        message.is_multipart.return_value = False
        message_from_bytes.return_value = message

        result = process_account(account, Mock(scanned_keyword="SpamFilterScanned", max_messages_per_account=1), lambda body: (False, "ham"), dry_run=False)

    assert result.evaluated == 1
    assert result.ham == 1
    assert result.moved == 0


def test_process_account_uses_imap_peek_for_fetch(account):
    mail = Mock()
    mail.capabilities = b"MOVE"
    mail.select.return_value = ("OK", None)
    mail.uid.side_effect = [
        ("OK", [b"1"]),
        ("OK", [(b"1", b"Subject: hi\n\nhello")]),
    ]
    mail.logout.return_value = ("BYE", None)

    with patch("mail_client.imaplib.IMAP4_SSL", return_value=mail), patch("mail_client.email.message_from_bytes") as message_from_bytes:
        message = Mock()
        message.get.return_value = "Subject"
        message.get_content_type.return_value = "text/plain"
        message.get_payload.return_value = b"hello"
        message.get_content_charset.return_value = None
        message.is_multipart.return_value = False
        message_from_bytes.return_value = message

        process_account(account, Mock(scanned_keyword="SpamFilterScanned", max_messages_per_account=1), lambda body: (False, "ham"), dry_run=True)

    assert mail.uid.call_args_list[1].args[2] == "(BODY.PEEK[])"
