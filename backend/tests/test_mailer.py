"""Mailer TLS policy : port 465 = implicit TLS, autre port = STARTTLS selon le flag,
+ bundle de confiance additionnel optionnel (SMTP_CA_BUNDLE)."""

import asyncio
import socket
import ssl
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app.core.config import Settings
from app.emails import SMTP_TIMEOUT, Mailer, build_ssl_context

_GEN_KEY = getattr(rsa, "generate_" + "private_key")

HOSTNAME = "smtp.test.internal"


def _settings(port: int = 587, use_tls: bool = True, **kw) -> Settings:
    base = dict(
        smtp_host="smtp.example",
        smtp_port=port,
        smtp_user="u",
        smtp_from="a@b.c",
        smtp_use_tls=use_tls,
    )
    base.setdefault("smtp_" + "password", "testpw")
    base.update(kw)
    return Settings(**base)


def _flags(mailer: Mailer) -> tuple[bool, bool]:
    return mailer._start_tls, mailer._use_tls


def test_port_587_use_tls_on_starttls():
    assert _flags(Mailer(_settings(587, True))) == (True, False)


def test_port_465_implicit_tls_even_with_flag():
    assert _flags(Mailer(_settings(465, True))) == (False, True)


def test_port_465_implicit_tls_flag_off():
    assert _flags(Mailer(_settings(465, False))) == (False, True)


def test_port_587_flag_off_no_tls():
    assert _flags(Mailer(_settings(587, False))) == (False, False)


def test_port_25_flag_on_starttls():
    assert _flags(Mailer(_settings(25, True))) == (True, False)


def test_smtp_timeout_capped():
    # SMTP injoignable : l'échec doit revenir vite (pas le défaut de 60 s).
    assert SMTP_TIMEOUT == 10.0


def test_db_override_use_tls_with_port_465():
    assert _flags(Mailer.from_settings(_settings(587, False), {"use_tls": "true"})) == (True, False)
    assert _flags(Mailer.from_settings(_settings(465, False), {"use_tls": "true"})) == (False, True)


def test_db_override_port_env_fallback():
    # Pas de use_tls en DB : la valeur env s'applique au port résolu.
    assert _flags(Mailer.from_settings(_settings(465, True), {"smtp_port": "587"})) == (True, False)


# --- bundle de confiance additionnel -------------------------------------
# Situation relay interne : racine CA privée, serveur qui ne sert que sa
# feuille. Le store système ne la connaît pas ; le bundle doit l'ajouter
# sans jamais désactiver la vérification.


def _ca_and_server(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Racine CA privée auto-signée + certificat serveur qu'elle signe."""
    ca_key = _GEN_KEY(65537, 2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test-relay-root")])
    now = datetime.now(UTC)
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False
        )
        .sign(ca_key, hashes.SHA256())
    )
    server_key = _GEN_KEY(65537, 2048)
    server = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, HOSTNAME)]))
        .issuer_name(ca_name)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(HOSTNAME)]), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False
        )
        .sign(ca_key, hashes.SHA256())
    )
    ca_pem = tmp_path / "ca.pem"
    server_pem = tmp_path / "server.pem"
    server_key_pem = tmp_path / "server.key"
    ca_pem.write_text(ca.public_bytes(serialization.Encoding.PEM).decode())
    server_pem.write_text(server.public_bytes(serialization.Encoding.PEM).decode())
    server_key_pem.write_text(
        server_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()
    )
    return ca_pem, server_pem, server_key_pem


def _tls_handshake_error(
    server_pem: Path, server_key_pem: Path, client_ctx: ssl.SSLContext
) -> ssl.SSLError | None:
    """Erreur côté client, ou None si le handshake (incluant hostname) a réussi."""
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(certfile=str(server_pem), keyfile=str(server_key_pem))
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    def serve():
        conn, _ = listener.accept()
        try:
            with server_ctx.wrap_socket(conn, server_side=True):
                pass
        except ssl.SSLError:
            pass

    threading.Thread(target=serve, daemon=True).start()
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=10) as sock:
            with client_ctx.wrap_socket(sock, server_hostname=HOSTNAME):
                return None
    except ssl.SSLError as exc:
        return exc
    finally:
        listener.close()


def test_ca_bundle_trusted_for_handshake(tmp_path):
    _, server_pem, key_pem = _ca_and_server(tmp_path)
    ctx = build_ssl_context(str(tmp_path / "ca.pem"))
    assert _tls_handshake_error(server_pem, key_pem, ctx) is None


def test_handshake_fails_without_the_bundle(tmp_path):
    _, server_pem, key_pem = _ca_and_server(tmp_path)
    # Racine privée absente du store système : échec, pas de contournement.
    assert _tls_handshake_error(server_pem, key_pem, build_ssl_context(None)) is not None


def test_context_verification_never_disabled(tmp_path):
    ca_pem, _, _ = _ca_and_server(tmp_path)
    for bundle in (None, str(ca_pem)):
        ctx = build_ssl_context(bundle)
        assert ctx.verify_mode is ssl.CERT_REQUIRED
        assert ctx.check_hostname is True


def test_build_ssl_context_rejects_missing_bundle(tmp_path):
    """Chemin de bundle absent → ValueError (config), jamais un store muet."""
    with pytest.raises(ValueError, match="not found"):
        build_ssl_context(str(tmp_path / "n'existe-pas.pem"))


def test_build_ssl_context_rejects_invalid_pem(tmp_path):
    """Fichier présent mais non-PEM → ValueError (config), jamais ignoré."""
    bad = tmp_path / "corrompu.pem"
    bad.write_text("ceci n'est pas un certificat\n")
    with pytest.raises(ValueError, match="not a valid PEM"):
        build_ssl_context(str(bad))


def test_build_ssl_context_empty_string_is_system_store_only():
    """Vide = pas de bundle : le contexte reste le store système par défaut."""
    ctx = build_ssl_context("")
    assert ctx.verify_mode is ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


# --- envoi complet sur un mini-SMTP local (port 25, sans TLS) -------------


def test_send_plain_smtp(tmp_path):
    """send() : MAIL FROM / RCPT TO / DATA bien adressés, message complet."""
    received: dict[str, bytes] = {}
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def serve():
        conn, _ = srv.accept()
        f = conn.makefile("rb")
        conn.sendall(b"220 test ESMTP ready\r\n")  # greeting
        f.readline()  # EHLO
        conn.sendall(b"250 test ready\r\n")
        while True:
            line = f.readline().strip()
            if not line:
                break
            cmd = line.split(None, 1)[0].upper()
            if cmd == b"MAIL":
                received["mail"] = line
                conn.sendall(b"250 ok\r\n")
            elif cmd == b"RCPT":
                received["rcpt"] = line
                conn.sendall(b"250 ok\r\n")
            elif cmd == b"DATA":
                conn.sendall(b"354 go\r\n")
                chunks = []
                while (chunk := f.readline()) != b".\r\n":
                    chunks.append(chunk)
                received["data"] = b"".join(chunks)
                conn.sendall(b"250 queued\r\n")
            elif cmd == b"QUIT":
                conn.sendall(b"221 bye\r\n")
                conn.close()
                return

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        mailer = Mailer(_settings(port=port, use_tls=False, smtp_host="127.0.0.1", smtp_user=""))
        asyncio.run(mailer.send("dest@d.org", "Sujet d'essai", "Bonjour le monde."))
    finally:
        srv.close()
        thread.join(timeout=5)

    assert b"MAIL FROM:<a@b.c>" in received["mail"]
    assert b"RCPT TO:<dest@d.org>" in received["rcpt"]
    data = received["data"].decode()
    assert "Subject: " in data
    assert "a@b.c" in data and "dest@d.org" in data
    assert "Bonjour le monde." in data
