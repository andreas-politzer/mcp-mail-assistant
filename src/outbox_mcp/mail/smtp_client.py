"""SMTP delivery with a clear split between 'certainly not sent' and 'result unknown'."""

from __future__ import annotations

import smtplib
import socket
import ssl
from email import policy
from email.message import EmailMessage
from typing import Optional


class SmtpSendFailed(Exception):
    """The SMTP server provably did not accept the message."""


class SmtpSendUnknown(Exception):
    """The connection broke after the message may have been handed over."""


class SmtpClient:
    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        security: str = "starttls",
        timeout: float = 30.0,
        helo: Optional[str] = None,
        ssl_context: Optional[ssl.SSLContext] = None,
    ) -> None:
        self.host, self.port, self.user, self._password = host, port, user, password
        self.security, self.timeout, self.helo = security, timeout, helo
        self.ssl_context = ssl_context

    def _clean(self, exc: BaseException) -> str:
        text = " ".join(f"{exc.__class__.__name__}: {exc}".split())
        if self._password:
            text = text.replace(self._password, "***")
        return text[:300]

    def send(self, message: EmailMessage, envelope_from: str, envelope_to: str) -> None:
        context = self.ssl_context or ssl.create_default_context()
        server: Optional[smtplib.SMTP] = None
        # Phase 1: everything up to the DATA command. Any error here means: not sent.
        try:
            kwargs = {"timeout": self.timeout}
            if self.helo:
                kwargs["local_hostname"] = self.helo
            if self.security == "ssl":
                server = smtplib.SMTP_SSL(self.host, self.port, context=context, **kwargs)
            else:
                server = smtplib.SMTP(self.host, self.port, **kwargs)
            server.ehlo()
            if self.security == "starttls":
                if not server.has_extn("starttls"):
                    raise smtplib.SMTPNotSupportedError("server does not offer STARTTLS")
                server.starttls(context=context)
                server.ehlo()
            if self.user:
                server.login(self.user, self._password)
            server.mail(envelope_from)
            code, resp = server.rcpt(envelope_to)
            if code not in (250, 251):
                raise smtplib.SMTPRecipientsRefused({envelope_to: (code, resp)})
        except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
            self._close(server)
            raise SmtpSendFailed(self._clean(exc)) from None

        # Phase 2: the DATA command. From here on a broken connection means: unknown.
        try:
            # SMTP requires CRLF line endings; smtplib does not convert bytes, so build them correctly.
            code, resp = server.data(message.as_bytes(policy=policy.SMTP))
        except smtplib.SMTPDataError as exc:
            self._close(server)
            raise SmtpSendFailed(self._clean(exc)) from None
        except (smtplib.SMTPException, OSError, ssl.SSLError, socket.timeout) as exc:
            self._close(server)
            raise SmtpSendUnknown(self._clean(exc)) from None
        # smtplib.data() does not raise for a rejection after the message body: only 250 means accepted.
        if code != 250:
            self._close(server)
            raise SmtpSendFailed(self._clean(smtplib.SMTPDataError(code, resp)))
        # Accepted. Problems while quitting do not matter any more.
        try:
            server.quit()
        except Exception:
            self._close(server)

    @staticmethod
    def _close(server: Optional[smtplib.SMTP]) -> None:
        if server is None:
            return
        try:
            server.close()
        except Exception:
            pass
