"""Source-only desktop QA seed. Excluded from the distributed Python sidecar."""
import os


def seed():
    os.environ["FUNDS_TRACE_LICENSE_SECRET"] = "synthetic-desktop-only-not-for-use"
    from app import db, importer, license as licensing
    from scripts.preview_synthetic import graph_fixture_rows

    licensing.save_key(licensing.issue(licensing.machine_code()))
    for name, isolated in [("合成演示 · 流水核对", False), ("合成演示 · 同账号隔离对照", True)]:
        case = db.q("insert into cases(name) values (?) returning id", [name])[0]["id"]
        rows = graph_fixture_rows(200, "hub", 2026, isolated=isolated)
        importer.commit_rows("synthetic-desktop.csv", "A", rows, case_id=case)
        db.x("insert into tracked_flows(case_id,flow_id,canvas_id) select case_id,id,0 from flows where case_id=?", [case])
    db.x("insert into cases(name) values (?)", ["合成演示 · 空白案件"])
