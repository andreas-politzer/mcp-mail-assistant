"""Builds the actual e-mail. Fixed sender, exactly one recipient, no CC/BCC, no attachments (yet)."""

from __future__ import annotations

from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from email.headerregistry import Address
from typing import Optional


def _address(name: str, address: str) -> Address:
    local, _, domain = address.rpartition("@")
    return Address(display_name=name, username=local, domain=domain)


def build_message(
    from_address: str,
    from_name: str,
    to_address: str,
    to_name: str,
    subject: str,
    body: str,
    message_id_domain: Optional[str] = None,
) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = _address(from_name, from_address)
    msg["To"] = _address(to_name, to_address)
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=message_id_domain or from_address.rpartition("@")[2])
    msg.set_content(body, subtype="plain", charset="utf-8")
    return msg
