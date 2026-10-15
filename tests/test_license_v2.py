"""Public RFC 8032 test seed only; no real licensing configuration is inherited."""
import base64
from datetime import date, datetime
import hashlib
import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa

from app import license as licensing


MACHINE = "ABCD-0123-4567-89EF"
OTHER_MACHINE = "ABCD-0123-4567-89EE"
# RFC 8032 section 7.1 TEST 1, intentionally public and never used for deployment.
RFC_TEST_SEED = bytes.fromhex(
    "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60"
)
DOMAIN = b"FundsTrace-license-v2\0"


def b64(raw):
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def payload(expiry="0"):
    return {"v": 2, "p": "FundsTrace",
            "m": hashlib.sha256(MACHINE.encode()).hexdigest()[:16].upper(), "e": expiry}


@pytest.fixture(autouse=True)
def isolated_configuration(tmp_path, monkeypatch):
    monkeypatch.delenv("FUNDS_TRACE_LICENSE_SECRET", raising=False)
    monkeypatch.delenv("FUNDS_TRACE_LICENSE_PUBLIC_KEY_FILE", raising=False)
    monkeypatch.setattr(licensing, "_PUBLIC_KEY_PATH", tmp_path / "absent-public-key.pem")
    monkeypatch.setattr(licensing, "LICENSE_PATH", str(tmp_path / "runtime" / "license.dat"))
    monkeypatch.setattr(licensing, "machine_code", lambda: MACHINE)

    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 15)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 15, 12, 0, tzinfo=tz)

    monkeypatch.setattr(licensing, "date", FixedDate)
    monkeypatch.setattr(licensing, "datetime", FixedDateTime)


@pytest.fixture
def signer(tmp_path, monkeypatch):
    private = ed25519.Ed25519PrivateKey.from_private_bytes(RFC_TEST_SEED)
    public = private.public_key()
    public_path = tmp_path / "公开测试公钥.pem"
    public_path.write_bytes(public.public_bytes(serialization.Encoding.PEM,
                                               serialization.PublicFormat.SubjectPublicKeyInfo))
    monkeypatch.setenv("FUNDS_TRACE_LICENSE_PUBLIC_KEY_FILE", str(public_path))

    def issue(value=None, *, raw=None, domain=DOMAIN):
        raw = raw if raw is not None else json.dumps(
            payload() if value is None else value, separators=(",", ":")
        ).encode("utf-8")
        return "FT2." + b64(raw) + "." + b64(private.sign(domain + raw))

    return issue, public_path, public


def test_public_rfc_vector_and_permanent_token(signer):
    issue, _, public = signer
    assert public.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex() == (
        "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"
    )
    assert licensing.verify(issue(), "  abcd-0123-4567-89ef  ") == "0"
    assert licensing.verify(issue(), OTHER_MACHINE) is None
    assert licensing.verify(issue(), "SYNTHETIC-NONSTANDARD-MACHINE") is None


def test_signature_covers_original_payload_and_product_domain(signer):
    issue, _, _ = signer
    original = issue()
    _, _, signature = original.split(".")
    changed = json.dumps(payload("20271015"), separators=(",", ":")).encode()
    assert licensing.verify("FT2." + b64(changed) + "." + signature, MACHINE) is None
    assert licensing.verify(issue(domain=b"another-product\0"), MACHINE) is None
    # A valid signature over differently spaced JSON must verify those exact bytes.
    assert licensing.verify(issue(raw=json.dumps(payload(), indent=2).encode()), MACHINE) == "0"


def test_different_configured_public_key_rejects_token(signer, tmp_path, monkeypatch):
    issue, _, _ = signer
    token = issue()
    # RFC 8032 TEST 2 public key; no additional private key is created.
    wrong = ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(
        "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c"
    ))
    path = tmp_path / "other-public-key.pem"
    path.write_bytes(wrong.public_bytes(serialization.Encoding.PEM,
                                       serialization.PublicFormat.SubjectPublicKeyInfo))
    monkeypatch.setenv("FUNDS_TRACE_LICENSE_PUBLIC_KEY_FILE", str(path))
    assert licensing.verify(token, MACHINE) is None


@pytest.mark.parametrize("expiry,expected", [
    ("20261014", None), ("20261015", "20261015"), ("20261016", "20261016"),
    ("20260230", None), ("2026111", None), ("00000000", None),
])
def test_ft2_expiry_is_real_date_and_includes_current_day(signer, expiry, expected):
    issue, _, _ = signer
    assert licensing.verify(issue(payload(expiry)), MACHINE) == expected


@pytest.mark.parametrize("mutation", [
    {"v": True}, {"v": 2.0}, {"v": 3}, {"p": "OtherProduct"},
    {"m": "not-a-machine-hash"}, {"e": 0}, {"extra": "not-permitted"},
])
def test_strict_payload_contract(signer, mutation):
    issue, _, _ = signer
    assert licensing.verify(issue(payload() | mutation), MACHINE) is None


@pytest.mark.parametrize("raw", [
    b'{"v":2,"v":2,"p":"FundsTrace","m":"0000000000000000","e":"0"}',
    b'{"v":2,"p":"FundsTrace","e":"0"}', b"[]", b"null", b"\xff", b"{}" * 257,
])
def test_duplicate_missing_nonobject_and_oversized_payloads(signer, raw):
    issue, _, _ = signer
    assert licensing.verify(issue(raw=raw), MACHINE) is None


def test_strict_canonical_base64url_and_token_limits(signer):
    issue, _, _ = signer
    token = issue()
    prefix, encoded, signature = token.split(".")
    malformed = [
        " " + token, token + "\n", token.upper(), token + ".extra",
        f"{prefix}.{encoded}=.{signature}", f"{prefix}.{encoded}.{signature}=",
        f"{prefix}.{encoded}$.{signature}", f"{prefix}.{encoded}.{b64(b'not-64-bytes')}",
        f"{prefix}.{encoded}.{signature[:-1]}", "FT2." + "A" * 4096 + ".AA",
    ]
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    last = alphabet.index(signature[-1])
    # Ed25519's 64-byte signature leaves four unused trailing base64 bits.
    malformed.append(f"{prefix}.{encoded}.{signature[:-1]}{alphabet[last + 1]}")
    for value in malformed:
        assert licensing.verify(value, MACHINE) is None


def test_unconfigured_public_key_does_not_accept_even_valid_token(signer, monkeypatch):
    issue, _, _ = signer
    token = issue()
    monkeypatch.delenv("FUNDS_TRACE_LICENSE_PUBLIC_KEY_FILE")
    assert licensing.verify(token, MACHINE) is None
    assert licensing.status() == {"activated": False, "machine_code": MACHINE,
                                  "expiry": "", "permanent": False,
                                  "verification_mode": "unconfigured", "public_key_fingerprint": ""}


@pytest.mark.parametrize("kind", ["missing", "relative", "directory", "oversized", "private", "rsa"])
def test_invalid_public_configuration_fails_closed(signer, tmp_path, monkeypatch, kind):
    issue, _, _ = signer
    token = issue()
    path = tmp_path / "invalid-public-key.pem"
    if kind == "relative":
        monkeypatch.setenv("FUNDS_TRACE_LICENSE_PUBLIC_KEY_FILE", "relative-public-key.pem")
    else:
        if kind == "directory":
            path.mkdir()
        elif kind == "oversized":
            path.write_bytes(b"X" * 4097)
        elif kind == "private":
            path.write_bytes(b"-----BEGIN PRIVATE KEY-----\nPUBLIC-TEST-ONLY\n-----END PRIVATE KEY-----\n")
        elif kind == "rsa":
            public = rsa.RSAPublicNumbers(65537, (1 << 1023) + 643).public_key()
            path.write_bytes(public.public_bytes(serialization.Encoding.PEM,
                                                 serialization.PublicFormat.SubjectPublicKeyInfo))
        monkeypatch.setenv("FUNDS_TRACE_LICENSE_PUBLIC_KEY_FILE", str(path))
    assert licensing.verify(token, MACHINE) is None
    assert licensing.status()["verification_mode"] == "unconfigured"
    assert licensing.status()["public_key_fingerprint"] == ""


def test_default_bundled_public_key_and_status_fields(signer, monkeypatch):
    issue, public_path, public = signer
    monkeypatch.delenv("FUNDS_TRACE_LICENSE_PUBLIC_KEY_FILE")
    monkeypatch.setattr(licensing, "_PUBLIC_KEY_PATH", public_path)
    licensing.save_key(issue(payload("20261015")))
    der = public.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    assert licensing.status() == {
        "activated": True, "machine_code": MACHINE, "expiry": "2026-10-15", "permanent": False,
        "verification_mode": "public-key",
        "public_key_fingerprint": hashlib.sha256(der).hexdigest()[:16].upper(),
    }
    assert licensing.is_activated()


def test_legacy_hmac_requires_explicit_secret_and_keeps_expiry_semantics(signer, monkeypatch):
    monkeypatch.setenv("FUNDS_TRACE_LICENSE_SECRET", "synthetic-legacy-key-not-for-use")
    permanent = licensing.issue("SYNTHETIC-OLD-MACHINE")
    assert licensing.verify(permanent.lower(), "SYNTHETIC-OLD-MACHINE") == "0"
    assert licensing.verify(permanent, "OTHER-OLD-MACHINE") is None
    today_body = licensing._machine_hash(MACHINE) + ".20261015"
    import hmac
    signature = hmac.new(b"synthetic-legacy-key-not-for-use", today_body.encode(), hashlib.sha256)
    assert licensing.verify(today_body + "." + signature.hexdigest()[:32], MACHINE) is None
    licensing.save_key(licensing.issue(MACHINE))
    st = licensing.status()
    assert st["activated"] and st["permanent"] and st["verification_mode"] == "legacy-hmac"
    assert st["public_key_fingerprint"]  # Configured FT2 key remains visible for migration.
    monkeypatch.delenv("FUNDS_TRACE_LICENSE_SECRET")
    assert licensing.verify(permanent, "SYNTHETIC-OLD-MACHINE") is None
    with pytest.raises(RuntimeError):
        licensing.issue(MACHINE)
