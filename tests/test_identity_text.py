"""Portable identity admission retains exact text and control boundaries."""
import json

import pytest

from app import evidence_resolution as evidence


def manifest(bank):
    return json.dumps({"role": "manifest", "scope_eligible": "yes", "request": "SYN-001", "bank": bank})


def test_manifest_admits_printable_ascii_and_rejects_every_ascii_control():
    for code in range(128):
        bank = "SYN" + chr(code) + "BANK"
        if 32 <= code <= 126:
            assert evidence.validate_evidence(manifest(bank))["bank"] == bank
        else:
            with pytest.raises(ValueError, match="invalid identity text"):
                evidence.validate_evidence(manifest(bank))


@pytest.mark.parametrize("bank", ["", "0000123", "银行", "é", "e\u0301", "SYN\u00a0BANK", "SYN\u2028BANK", "A" * 512])
def test_identity_text_preserves_unicode_spaces_composition_and_leading_zeros(bank):
    assert evidence.validate_evidence(manifest(bank))["bank"] == bank


@pytest.mark.parametrize("bank", [123, None, True, "A" * 513, " SYN", "SYN ", "SYN\u0085BANK", "SYN\u200bBANK", "SYN\ufeffBANK"])
def test_identity_text_rejects_types_padding_overlength_and_unicode_controls(bank):
    with pytest.raises(ValueError, match="invalid identity text"):
        evidence.validate_evidence(manifest(bank))


def test_missing_markers_are_retained_until_projection_and_input_is_not_reused():
    raw = json.dumps({"role": "account", "scope_eligible": "yes", "account": "null", "card": "0000123",
                      "bank": "N/A", "name": "Synthetic", "currency": "CNY"})
    parsed = evidence.validate_evidence(raw)
    assert parsed["account"] == "null" and parsed["bank"] == "N/A"
    projected = evidence._projection_evidence(parsed)
    assert projected["account"] == projected["bank"] == ""
    assert projected["card"] == "0000123"
    assert evidence.validate_evidence(raw)["account"] == "null"
