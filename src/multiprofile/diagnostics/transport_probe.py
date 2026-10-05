"""What a destination sees of a browser's TLS, parsed from the ClientHello.

The parity roadmap is specific about why this exists and about how it can be
misused: *"Do not declare transport alignment from a JA3/JA4 string without a
matched reference and controlled route."* A fingerprint on its own says
nothing. Two fingerprints captured in the same session, on the same host,
from the same destination, say something.

So this module only parses. It computes nothing about whether a value is
"good", because there is no such thing without a reference.

**Why a ClientHello and not a completed handshake.** The ClientHello is the
first flight: it carries the offered cipher suites, the extension list and
their order, the supported groups, the signature algorithms and the ALPN
offer. All of it is chosen by the client before the server has said anything,
which is exactly what makes it a fingerprint -- and it means a destination can
record it without a certificate the browser would have to trust. Observing
HTTP/2 SETTINGS or HTTP/3 needs a completed handshake and therefore a trusted
certificate; that is a separate, larger piece of work and is recorded as a
remaining gate rather than guessed at.

**GREASE.** RFC 8701 has clients advertise reserved values to keep the
ecosystem tolerant of unknown ones. Chromium does this, and the values it
picks vary **per connection**, so any fingerprint that includes them is
unstable by design. Both forms are computed here: the raw JA3 as originally
specified, and a GREASE-stripped form. A comparison must use the stripped
one, or it compares randomness.
"""
from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass, field
from typing import Any

#: RFC 8701 reserved values, which clients send to exercise tolerance of
#: unknown codepoints. The pattern is 0x?a?a for both bytes equal and the low
#: nibble 0xa.
GREASE_VALUES = frozenset(
    (v << 8) | v for v in (0x0A, 0x1A, 0x2A, 0x3A, 0x4A, 0x5A, 0x6A, 0x7A,
                           0x8A, 0x9A, 0xAA, 0xBA, 0xCA, 0xDA, 0xEA, 0xFA)
)


def is_grease(value: int) -> bool:
    return value in GREASE_VALUES


class ClientHelloError(ValueError):
    """The bytes were not a TLS ClientHello this parser understands."""


@dataclass
class ClientHello:
    """One observed first flight, as fields rather than as a verdict."""

    record_version: int
    handshake_version: int
    cipher_suites: list[int] = field(default_factory=list)
    extensions: list[int] = field(default_factory=list)
    supported_groups: list[int] = field(default_factory=list)
    ec_point_formats: list[int] = field(default_factory=list)
    signature_algorithms: list[int] = field(default_factory=list)
    supported_versions: list[int] = field(default_factory=list)
    alpn: list[str] = field(default_factory=list)
    server_name: str | None = None
    compression_methods: list[int] = field(default_factory=list)
    session_id_length: int = 0

    # ---- derived -------------------------------------------------------
    def ja3_string(self, *, strip_grease: bool = False) -> str:
        """The JA3 field order: version,ciphers,extensions,curves,formats."""
        def keep(values: list[int]) -> list[int]:
            return [v for v in values if not is_grease(v)] if strip_grease else list(values)

        parts = [
            str(self.handshake_version),
            "-".join(str(v) for v in keep(self.cipher_suites)),
            "-".join(str(v) for v in keep(self.extensions)),
            "-".join(str(v) for v in keep(self.supported_groups)),
            "-".join(str(v) for v in self.ec_point_formats),
        ]
        return ",".join(parts)

    def ja3(self, *, strip_grease: bool = False) -> str:
        return hashlib.md5(  # noqa: S324 - JA3 is defined as MD5; not a security use
            self.ja3_string(strip_grease=strip_grease).encode()
        ).hexdigest()

    def ja4_like(self) -> str:
        """A JA4-shaped summary. **Not** a conformant JA4.

        JA4 has a precise specification; this is the same *idea* -- a readable
        prefix plus sorted, GREASE-free digests -- written here so a
        comparison has something stable and legible to compare. It is named
        ``ja4_like`` so nobody quotes it as a JA4 to a vendor.
        """
        ciphers = sorted(v for v in self.cipher_suites if not is_grease(v))
        extensions = sorted(v for v in self.extensions if not is_grease(v))
        # GREASE first: Chromium puts a reserved codepoint in
        # supported_versions too, and those values are numerically larger
        # than 0x0304, so `max` over the raw list picked the GREASE value and
        # the version label changed on every connection.
        real_versions = [v for v in self.supported_versions if not is_grease(v)]
        highest = max(real_versions or [self.handshake_version])
        version_label = {
            0x0304: "13", 0x0303: "12", 0x0302: "11", 0x0301: "10",
        }.get(highest, hex(highest))
        first_alpn = (self.alpn[0] if self.alpn else "00")[:2]
        prefix = (
            f"t{version_label}"
            f"{'d' if self.server_name else 'i'}"
            f"{min(len(ciphers), 99):02d}"
            f"{min(len(extensions), 99):02d}"
            f"{first_alpn}"
        )
        cipher_digest = hashlib.sha256(
            ",".join(str(v) for v in ciphers).encode()).hexdigest()[:12]
        extension_digest = hashlib.sha256(
            ",".join(str(v) for v in extensions).encode()).hexdigest()[:12]
        return f"{prefix}_{cipher_digest}_{extension_digest}"

    def to_dict(self) -> dict[str, Any]:
        """Everything observed, plus both fingerprint forms.

        Carries no IP, no SNI for anything but the lab's own host, and no
        session material.
        """
        return {
            "recordVersion": hex(self.record_version),
            "handshakeVersion": hex(self.handshake_version),
            "supportedVersions": [hex(v) for v in self.supported_versions],
            "supportedVersionsNoGrease": [
                hex(v) for v in self.supported_versions if not is_grease(v)],
            "cipherCount": len(self.cipher_suites),
            "cipherSuites": self.cipher_suites,
            "greaseCiphers": [v for v in self.cipher_suites if is_grease(v)],
            "extensionCount": len(self.extensions),
            # Order matters and is part of the fingerprint, so it is kept.
            "extensions": self.extensions,
            "greaseExtensions": [v for v in self.extensions if is_grease(v)],
            "supportedGroups": self.supported_groups,
            "ecPointFormats": self.ec_point_formats,
            "signatureAlgorithms": self.signature_algorithms,
            "alpn": list(self.alpn),
            "serverName": self.server_name,
            "compressionMethods": self.compression_methods,
            "sessionIdLength": self.session_id_length,
            "ja3": self.ja3(),
            "ja3String": self.ja3_string(),
            "ja3NoGrease": self.ja3(strip_grease=True),
            "ja3StringNoGrease": self.ja3_string(strip_grease=True),
            "ja4Like": self.ja4_like(),
        }


def _u16(buf: bytes, offset: int) -> int:
    return struct.unpack_from("!H", buf, offset)[0]


def parse_client_hello(data: bytes) -> ClientHello:
    """Parse a TLS record containing a ClientHello.

    Raises :class:`ClientHelloError` rather than returning something
    half-parsed: a fingerprint computed from a misread buffer is worse than
    no fingerprint, because it looks like a measurement.
    """
    if len(data) < 5:
        raise ClientHelloError("short of a TLS record header")
    if data[0] != 0x16:
        raise ClientHelloError(f"not a handshake record (type {data[0]:#x})")
    record_version = _u16(data, 1)
    record_length = _u16(data, 3)
    body = data[5:5 + record_length]
    if len(body) < 4:
        raise ClientHelloError("short of a handshake header")
    if body[0] != 0x01:
        raise ClientHelloError(f"not a ClientHello (handshake type {body[0]:#x})")

    offset = 4  # handshake type + 3-byte length
    if len(body) < offset + 34:
        raise ClientHelloError("short of the client version and random")
    handshake_version = _u16(body, offset)
    offset += 2 + 32  # version + random

    session_id_length = body[offset]
    offset += 1 + session_id_length

    if len(body) < offset + 2:
        raise ClientHelloError("short of the cipher suite list")
    cipher_bytes = _u16(body, offset)
    offset += 2
    cipher_suites = [
        _u16(body, offset + i) for i in range(0, cipher_bytes, 2)
    ]
    offset += cipher_bytes

    compression_count = body[offset]
    offset += 1
    compression_methods = list(body[offset:offset + compression_count])
    offset += compression_count

    hello = ClientHello(
        record_version=record_version,
        handshake_version=handshake_version,
        cipher_suites=cipher_suites,
        compression_methods=compression_methods,
        session_id_length=session_id_length,
    )

    if len(body) < offset + 2:
        # Legal but not something a browser sends; nothing more to read.
        return hello
    extensions_length = _u16(body, offset)
    offset += 2
    end = min(offset + extensions_length, len(body))

    while offset + 4 <= end:
        ext_type = _u16(body, offset)
        ext_length = _u16(body, offset + 2)
        payload = body[offset + 4:offset + 4 + ext_length]
        offset += 4 + ext_length
        hello.extensions.append(ext_type)

        if ext_type == 0x000A and len(payload) >= 2:        # supported_groups
            count = _u16(payload, 0)
            hello.supported_groups = [
                _u16(payload, 2 + i) for i in range(0, count, 2)
            ]
        elif ext_type == 0x000B and payload:                 # ec_point_formats
            hello.ec_point_formats = list(payload[1:1 + payload[0]])
        elif ext_type == 0x000D and len(payload) >= 2:       # signature_algorithms
            count = _u16(payload, 0)
            hello.signature_algorithms = [
                _u16(payload, 2 + i) for i in range(0, count, 2)
            ]
        elif ext_type == 0x002B and payload:                 # supported_versions
            hello.supported_versions = [
                _u16(payload, 1 + i) for i in range(0, payload[0], 2)
            ]
        elif ext_type == 0x0010 and len(payload) >= 2:       # ALPN
            list_length = _u16(payload, 0)
            cursor = 2
            names: list[str] = []
            while cursor < 2 + list_length and cursor < len(payload):
                name_length = payload[cursor]
                names.append(
                    payload[cursor + 1:cursor + 1 + name_length].decode(
                        "latin-1", "replace")
                )
                cursor += 1 + name_length
            hello.alpn = names
        elif ext_type == 0x0000 and len(payload) >= 5:       # server_name
            name_length = _u16(payload, 3)
            hello.server_name = payload[5:5 + name_length].decode(
                "latin-1", "replace")

    return hello


#: Why JA3 cannot be used to compare two Chromium connections.
#:
#: Measured on Chromium 151: sixteen ClientHellos captured from **one**
#: browser run produced **sixteen distinct extension orders**, one distinct
#: extension *set*, and one distinct cipher list. Chromium shuffles the
#: extension order per connection on purpose, to stop the ecosystem
#: ossifying around a fixed layout (the same motivation as GREASE).
#:
#: JA3 concatenates the extension list *in order*, so a Chromium JA3 is a
#: fresh value on every connection. Two runs of the same binary disagree.
#: That makes it useless for the comparison this module exists to do, and it
#: is why real JA4 sorts the extension list before hashing.
JA3_IS_ORDER_DEPENDENT = (
    "JA3 includes the extension list in order, and Chromium randomises that "
    "order per connection (measured: 16 orders from 16 connections of one "
    "browser run, with one extension set). A Chromium JA3 therefore differs "
    "between two connections of the same binary and cannot be compared. Use "
    "the sorted forms."
)


def compare_hellos(product: ClientHello, reference: ClientHello) -> dict[str, Any]:
    """Where two observed first flights differ, field by field.

    **Order-insensitive where Chromium randomises order, and only there.**
    The extension list is compared as a set because Chromium shuffles it per
    connection; the cipher list, the supported groups and the signature
    algorithms are compared *in order*, because those are not shuffled and
    their order is part of the client's preference.

    GREASE is stripped everywhere: those codepoints are rerolled per
    connection too.
    """
    differences: list[dict[str, Any]] = []

    def compare(label: str, left: Any, right: Any) -> None:
        if left != right:
            differences.append({"field": label, "product": left,
                                "reference": right})

    def live(values: list[int]) -> list[int]:
        return [v for v in values if not is_grease(v)]

    compare("handshakeVersion", hex(product.handshake_version),
            hex(reference.handshake_version))
    compare("supportedVersions (GREASE stripped)",
            [hex(v) for v in live(product.supported_versions)],
            [hex(v) for v in live(reference.supported_versions)])
    # Ordered: Chromium does not shuffle these, and the order is a preference.
    compare("cipherSuites (ordered, GREASE stripped)",
            live(product.cipher_suites), live(reference.cipher_suites))
    compare("supportedGroups (ordered, GREASE stripped)",
            live(product.supported_groups), live(reference.supported_groups))
    compare("signatureAlgorithms (ordered)", product.signature_algorithms,
            reference.signature_algorithms)
    # Unordered: shuffled per connection by design. Comparing the order here
    # would report a difference between two runs of the same browser.
    compare("extensions (set, GREASE stripped)",
            sorted(live(product.extensions)), sorted(live(reference.extensions)))
    compare("ecPointFormats", product.ec_point_formats,
            reference.ec_point_formats)
    compare("alpn", product.alpn, reference.alpn)
    compare("compressionMethods", product.compression_methods,
            reference.compression_methods)

    return {
        "identical": not differences,
        # Reported for the record, and explicitly not used for equality.
        "ja3": {
            "product": product.ja3(strip_grease=True),
            "reference": reference.ja3(strip_grease=True),
            "comparable": False,
            "why": JA3_IS_ORDER_DEPENDENT,
        },
        "ja4Like": {
            "product": product.ja4_like(),
            "reference": reference.ja4_like(),
            "equal": product.ja4_like() == reference.ja4_like(),
        },
        "differences": differences,
    }
