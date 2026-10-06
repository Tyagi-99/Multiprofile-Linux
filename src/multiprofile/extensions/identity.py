"""The extension ID, predicted and then verified.

Chromium derives an **unpacked** extension's ID from the absolute path it was
loaded from: SHA-256 of the path, first 16 bytes, each nibble mapped to
``a``..``p``. A packed extension signed with a key uses the key instead, and a
manifest carrying a ``key`` pins the ID the same way.

That is why ``xdg.extension_payload_dir`` is stable for the life of an
extension and why updates replace the *contents* of that directory rather than
creating a versioned one beside it. Moving the payload would silently change
the extension's identity, and an extension's identity is where its storage,
its permissions grants and its site authorisations live -- a new ID is a new
extension with an empty profile, not an update.

Predicting the ID is useful only because it can then be **checked**. The
manager computes the expected value, launches, asks the browser what it
actually loaded, and records the observed ID. A mismatch is reported rather
than smoothed over: it means the browser did not load what we think it did.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

#: Chromium's alphabet for extension IDs: 16 hex nibbles mapped onto a..p.
_ALPHABET_BASE = ord("a")

ID_LENGTH = 32


def predict_unpacked_id(payload_path: str | Path) -> str:
    """The ID Chromium will give an unpacked extension at this absolute path.

    Verified against the browser on this host: the fixture at
    ``tests/fixtures/extensions/mp-test-ext`` predicts
    ``aelakinaflbnalldpkjgcjikjhljdonc`` and Chromium 151 reported exactly
    that from ``chrome.runtime.id`` inside the extension's own content script.
    """
    absolute = str(Path(payload_path).resolve())
    digest = hashlib.sha256(absolute.encode("utf-8")).hexdigest()[:ID_LENGTH]
    return "".join(chr(_ALPHABET_BASE + int(c, 16)) for c in digest)


def looks_like_extension_id(value: str | None) -> bool:
    """A Chrome extension ID is 32 characters from a..p."""
    if not value or len(value) != ID_LENGTH:
        return False
    return all("a" <= c <= "p" for c in value)
