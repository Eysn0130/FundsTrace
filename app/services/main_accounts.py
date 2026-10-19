"""Case-bound main-account classification shared by import and recognition."""

from app import db


def mark_automatic(case_id, account, name=""):
    """Record automatic evidence without replacing an explicit human decision.

    Callers own the encompassing case transaction. Repeated evidence retains
    the original marking time and fills a missing automatic name only.
    """
    account = account.strip()
    name = (name or "").strip()
    existing = db.q("select name, source from main_accounts where case_id=? and account=?",
                    [case_id, account])
    if existing:
        row = existing[0]
        if row["source"] != "auto":
            return {"account": account, "name": row["name"] or "", "source": "manual", "added": False}
        if name and not row["name"]:
            db.x("update main_accounts set name=? where case_id=? and account=?", [name, case_id, account])
        return {"account": account, "name": row["name"] or name, "source": "auto", "added": False}
    db.x("insert into main_accounts(case_id,account,name,source) values (?,?,?,'auto')",
         [case_id, account, name])
    return {"account": account, "name": name, "source": "auto", "added": True}
