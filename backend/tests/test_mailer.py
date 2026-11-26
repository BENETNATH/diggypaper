"""Mailer TLS policy : port 465 = implicit TLS, autre port = STARTTLS selon le flag."""

from app.core.config import Settings
from app.emails import Mailer


def _settings(port: int, use_tls: bool) -> Settings:
    return Settings(
        smtp_host="smtp.example",
        smtp_port=port,
        smtp_user="u",
        smtp_password="p",
        smtp_from="a@b.c",
        smtp_use_tls=use_tls,
    )


def _flags(mailer: Mailer) -> tuple[bool, bool]:
    cfg = mailer._mail.config
    return cfg.MAIL_STARTTLS, cfg.MAIL_SSL_TLS


def test_port_587_use_tls_on_starttls():
    starttls, ssl_tls = _flags(Mailer(_settings(587, True)))
    assert (starttls, ssl_tls) == (True, False)


def test_port_465_implicit_tls_even_with_flag():
    starttls, ssl_tls = _flags(Mailer(_settings(465, True)))
    assert (starttls, ssl_tls) == (False, True)


def test_port_465_implicit_tls_flag_off():
    starttls, ssl_tls = _flags(Mailer(_settings(465, False)))
    assert (starttls, ssl_tls) == (False, True)


def test_port_587_flag_off_no_tls():
    starttls, ssl_tls = _flags(Mailer(_settings(587, False)))
    assert (starttls, ssl_tls) == (False, False)


def test_port_25_flag_on_starttls():
    starttls, ssl_tls = _flags(Mailer(_settings(25, True)))
    assert (starttls, ssl_tls) == (True, False)


def test_smtp_timeout_capped_at_10s():
    # SMTP injoignable : l'échec doit revenir vite (pas le défaut de 60 s).
    assert Mailer(_settings(587, True))._mail.config.TIMEOUT == 10


def test_db_override_use_tls_with_port_465():
    mailer = Mailer.from_settings(_settings(587, False), {"use_tls": "true"})
    assert _flags(mailer) == (True, False)
    mailer = Mailer.from_settings(_settings(465, False), {"use_tls": "true"})
    assert _flags(mailer) == (False, True)
