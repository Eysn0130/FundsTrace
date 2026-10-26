"""Preserve unsupported directory attachments with an explicit coverage gap."""
from pathlib import PurePosixPath
import hashlib

REASONS = {"unsupported_attachment": "附件原件已保留，格式尚未适配，未解释其业务内容或计入资金统计", "non_business_attachment": "已识别Finder元数据附件，原件保留，不生成资金记录"}
DETAILS_FIELDS = frozenset({"attachment_kind"})


def plan(payload, path):
    from . import table_import
    name = PurePosixPath(path).name
    finder = (name == ".DS_Store" and payload[:8] == b"\0\0\0\1Bud1" or
              name.startswith("._") and payload[:8] == b"\0\5\x16\7\0\2\0\0")
    reason = "non_business_attachment" if finder else "unsupported_attachment"
    digest = hashlib.sha256(payload).hexdigest()
    return {"rows": [], "records": [{"record_no": 1, "member_path": path, "source_row": 1,
        "record_key": table_import.record_key(path, digest, 1), "disposition": "auxiliary" if finder else "unresolved", "reason_code": reason,
        "details": {"source_role": "attachment", "source_row": "1", "sheet": "", "financial_effect": "not_a_flow" if finder else "undetermined", "reason": REASONS[reason],
                    "attachment_kind": "finder_metadata" if finder else (PurePosixPath(path).suffix.lower() or "unknown")}}],
        "members": [{"member_path": path, "role": "attachment", "sha256": digest, "size_bytes": len(payload), "encoding": "binary", "row_count": 1}],
        "documents": [{"member_path": path, "role": "attachment", "row_count": 1}], "mapping_evidence": [], "columns": [], "mapping": {}, "mode": "B"}
