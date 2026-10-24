"""Check table write fidelity inside the import transaction.

This does not infer bank semantics. The ephemeral manifest binds accepted
parser facts to their actual stored links; no source cells or second ledger
are persisted. Its encoding deliberately does not use the deduplication key.
"""
import datetime as dt
from decimal import Decimal, localcontext
import hashlib
import json

from . import db, table_archive, table_cancellations, table_import


class IntegrityError(ValueError):
    """An internal plan or write disagrees with the accepted facts."""


_TEXT_FIELDS = ("trade_no", "payer_name", "payer_account", "payer_bank",
                "payee_name", "payee_account", "payee_bank", "currency", "summary", "channel")
_STORED_FIELDS = (*table_import.FLOW_FIELDS, "self_account", "self_name")


def _text(value):
    if value is None:
        return ""
    if not isinstance(value, str):
        raise IntegrityError("non-text canonical field")
    return value


def _money(value, *, nullable=False):
    if value is None and nullable:
        return None
    if not isinstance(value, Decimal) or not value.is_finite():
        raise IntegrityError("non-decimal canonical amount")
    with localcontext() as context:
        context.prec = 40
        if value != value.quantize(Decimal("0.01")):
            raise IntegrityError("non-cent canonical amount")
    return format(value, ".2f")


def _hash(values):
    return hashlib.sha256(json.dumps(values, ensure_ascii=False,
                                     separators=(",", ":")).encode()).digest()


def _facts(row):
    timestamp = row["trade_time"]
    if not isinstance(timestamp, dt.datetime) or timestamp.tzinfo is not None:
        raise IntegrityError("invalid canonical time")
    if row["amount"] is None or row["amount"] <= 0:
        raise IntegrityError("non-positive canonical amount")
    return (_hash([timestamp.isoformat(timespec="microseconds"),
                   *[_text(row[field]) for field in _TEXT_FIELDS],
                   _money(row["amount"]), _money(row.get("balance"), nullable=True)]),
            _hash([_text(row.get("self_account")), _text(row.get("self_name"))]))


def table_manifest(plan, rows):
    """Freeze compact expected facts before the writer can mutate its input."""
    accepted = {}
    for number, record in enumerate(plan["records"], 1):
        if record["record_no"] != number:
            raise IntegrityError("non-consecutive source records")
        if record["disposition"] == "accepted":
            accepted[number] = record
    manifest = {}
    for row in rows:
        position = row["_source_row"]
        record = accepted.get(position)
        if (record is None or position in manifest or row["_record_key"] != record["record_key"]
                or record["details"].get("flow_sha256") != table_import.flow_digest(row)):
            raise IntegrityError("accepted source facts disagree")
        manifest[position] = _facts(row)
    if manifest.keys() != accepted.keys():
        raise IntegrityError("accepted source facts missing")
    if plan["profile"] in table_archive.CANCELLATION_PROFILES:
        try:
            # Reconstruct the complete-member proof before it can exclude a
            # historical original from effective_flows. This reuses the package
            # proof checker; the separate write readback below is independent.
            table_cancellations.validate_evidence(plan["records"])
        except (ValueError, KeyError, TypeError) as error:
            raise IntegrityError("invalid cancellation evidence") from error
    return manifest


def verify_table_write(case_id, source_id, profile, manifest):
    """Read every new source link, including reused flows, before committing.

    Ordinary tables historically deduplicate thirteen facts, excluding self
    metadata. Preserve that rule for duplicates; inserted rows and all bank
    envelope links must also preserve the supplied self metadata.
    """
    cursor = db.con().execute(f"""
        select s.source_row,s.disposition,{','.join('f.' + name for name in _STORED_FIELDS)}
        from flow_sources s left join flows f on f.case_id=s.case_id and f.id=s.flow_id
        where s.case_id=? and s.source_id=?
        """, [case_id, source_id])
    while batch := cursor.fetchmany(1024):
        for position, disposition, *values in batch:
            expected = manifest.pop(position, None)
            if expected is None or disposition not in {"imported", "duplicate"}:
                raise IntegrityError("unexpected source link")
            actual = _facts(dict(zip(_STORED_FIELDS, values)))
            if (actual[0] != expected[0]
                    or (disposition == "imported" or profile in table_archive.SUPPORTED_PROFILES)
                    and actual[1] != expected[1]):
                raise IntegrityError("stored facts disagree")
    if manifest:
        raise IntegrityError("missing source link")
