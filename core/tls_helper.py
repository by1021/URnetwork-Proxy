"""Explicit/persistent gateway certificates and TLS streams using public MemoryBIO APIs."""
import asyncio
from datetime import datetime, timedelta, timezone
import ipaddress
import os
from pathlib import Path
import ssl
import subprocess
import tempfile
import threading

_CONTEXTS = {}
_CONTEXT_LOCK = threading.Lock()


class TLSConfigurationError(OSError):
    """Certificate setup failed; distinct from a busy listening port."""


def generate_self_signed_cert(cert_path, key_path, common_name="URnetwork-Proxy-Gateway"):
    """Generate in a private directory; never expose a world-readable private key."""
    cert_path, key_path = Path(cert_path), Path(key_path)
    cert_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=cert_path.parent) as directory:
        cert, key = Path(directory) / "cert.pem", Path(directory) / "key.pem"
        try:
            subprocess.run([
                "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                "-keyout", str(key), "-out", str(cert), "-days", "365",
                "-subj", f"/CN={common_name}",
                "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1,IP:::1",
            ], check=True, capture_output=True, timeout=30)
        except FileNotFoundError:
            # Optional fallback on hosts without an openssl executable.
            from cryptography import x509
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import rsa
            from cryptography.x509.oid import NameOID
            private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
            now = datetime.now(timezone.utc)
            certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                .public_key(private.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=365))
                .add_extension(x509.SubjectAlternativeName([
                    x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                    x509.IPAddress(ipaddress.ip_address("::1"))]), critical=False)
                .sign(private, hashes.SHA256()))
            key.write_bytes(private.private_bytes(serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
            cert.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        key.chmod(0o600)
        os.replace(key, key_path)
        os.replace(cert, cert_path)
    return True


def get_ssl_context(cert_file="", key_file="", state_dir=None, force_reload=False):
    """Explicit paths win; otherwise use this instance's local self-signed certificate.

    Never silently select another service's private key or replace bad explicit paths.
    """
    if bool(cert_file) != bool(key_file):
        raise ValueError("tls_cert_file and tls_key_file must be supplied together")
    with _CONTEXT_LOCK:
        if cert_file:
            cert, key = Path(cert_file), Path(key_file)
        else:
            directory = Path(state_dir) if state_dir else Path(__file__).resolve().parents[1] / ".local"
            cert, key = directory / "gateway_tls.crt", directory / "gateway_tls.key"
            if not cert.exists() and not key.exists():
                generate_self_signed_cert(cert, key)
        cache_key = tuple((str(p.resolve()), p.stat().st_mtime_ns, p.stat().st_size) for p in (cert, key))
        if cache_key not in _CONTEXTS or force_reload:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.options |= getattr(ssl, "OP_NO_RENEGOTIATION", 0)
            context.load_cert_chain(cert, key)
            context.set_alpn_protocols(["http/1.1"])
            # Keep a small cache; paths/mtime distinguish instances and certificate renewals.
            if len(_CONTEXTS) >= 16:
                _CONTEXTS.clear()
            _CONTEXTS[cache_key] = context
        return _CONTEXTS[cache_key]


class TLSStream:
    """StreamReader/Writer subset over TLS, including an already-consumed ClientHello.

    One reader and one writer may run concurrently on the same event loop. Raw
    StreamReader/Writer retain network backpressure; decrypted buffering is bounded
    by readuntil's header limit. No asyncio transport internals or start_tls patch.
    """
    def __init__(self, reader, writer, context, first=b"", *, server_side=True,
                 server_hostname=None, limit=32768):
        self.reader, self.writer = reader, writer
        self.incoming, self.outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
        self.ssl_object = context.wrap_bio(self.incoming, self.outgoing,
                                         server_side=server_side, server_hostname=server_hostname)
        self.incoming.write(first)
        self.buffer = bytearray()
        self.limit = limit
        self.eof = False

    def _flush(self):
        while self.outgoing.pending:
            self.writer.write(self.outgoing.read())

    async def _receive(self):
        self._flush()
        await self.writer.drain()
        data = await self.reader.read(65536)
        if data:
            self.incoming.write(data)
        elif not self.eof:
            self.eof = True
            self.incoming.write_eof()

    async def handshake(self):
        while True:
            try:
                self.ssl_object.do_handshake()
                self._flush()
                await self.writer.drain()
                return
            except ssl.SSLWantReadError:
                await self._receive()

    async def _read(self, size):
        while True:
            try:
                data = self.ssl_object.read(size)
                self._flush()
                return data
            except ssl.SSLWantReadError:
                await self._receive()
            except (ssl.SSLZeroReturnError, ssl.SSLEOFError):
                return b""

    async def read(self, n=-1):
        if n == 0:
            return b""
        if n < 0:
            chunks = []
            while chunk := await self.read(65536):
                chunks.append(chunk)
            return b"".join(chunks)
        if self.buffer:
            data = bytes(self.buffer[:n])
            del self.buffer[:n]
            return data
        return await self._read(n)

    async def readexactly(self, n):
        if n < 0:
            raise ValueError("readexactly size must not be negative")
        result = bytearray()
        while len(result) < n:
            chunk = await self.read(n - len(result))
            if not chunk:
                raise asyncio.IncompleteReadError(bytes(result), n)
            result.extend(chunk)
        return bytes(result)

    async def readuntil(self, separator=b"\n"):
        if not separator:
            raise ValueError("separator must not be empty")
        while True:
            offset = self.buffer.find(separator)
            if offset >= 0:
                if offset > self.limit:
                    raise asyncio.LimitOverrunError("TLS header too large", offset)
                end = offset + len(separator)
                result = bytes(self.buffer[:end])
                del self.buffer[:end]
                return result
            if len(self.buffer) > self.limit + len(separator):
                raise asyncio.LimitOverrunError("TLS header too large", len(self.buffer))
            chunk = await self._read(16384)
            if not chunk:
                partial = bytes(self.buffer)
                self.buffer.clear()
                raise asyncio.IncompleteReadError(partial, None)
            self.buffer.extend(chunk)

    def write(self, data):
        view = memoryview(data)
        while view:
            count = self.ssl_object.write(view[:16384])
            view = view[count:]
            self._flush()

    async def drain(self):
        self._flush()
        await self.writer.drain()

    def can_write_eof(self):
        return False

    def close(self):
        try:
            self.ssl_object.unwrap()
        except ssl.SSLError:
            pass
        finally:
            self._flush()
            self.writer.close()

    async def wait_closed(self):
        await self.writer.wait_closed()

    def get_extra_info(self, name, default=None):
        if name == "ssl_object":
            return self.ssl_object
        return self.writer.get_extra_info(name, default)
