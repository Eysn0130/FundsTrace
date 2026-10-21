"""Preview the built UI on loopback with disposable, synthetic state only."""
import argparse
import csv
from decimal import Decimal
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
from urllib.parse import unquote, urlsplit


GRAPH_ASSET_FILES = ("index.html", "app.js", "graph-view.js", "graph-layout.js", "graph-layout-worker.js", "graph-layout-quality.js", "graph-routing.js", "graph-route-quality.js", "graph-selection.js", "graph-benchmark.js",
                     "graph-relations.js", "graph-presentation.js", "graph-label-geometry.js", "ribbon-commands.js", "ribbon.js", "workspace-shell.js",
                     "style.css", "vendor/g6.min.js", "money.js", "logo.png", "favicon.ico", "favicon-32.png")


class _StaticReferences(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = set()

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if name not in ("src", "href") or not value:
                continue
            url = urlsplit(value)
            if not url.scheme and not url.netloc and url.path.startswith("/static/"):
                self.paths.add(unquote(url.path[len("/static/"):]))


def validate_graph_assets(directory):
    """An explicit renderer override is a complete static tree, never runtime data."""
    directory = Path(directory)
    if not directory.is_absolute():
        raise ValueError("graph-assets must be an absolute path to a complete static directory")
    if not directory.is_dir():
        raise ValueError("graph-assets must name an existing static directory")
    directory = directory.resolve()
    missing = [name for name in GRAPH_ASSET_FILES if not (directory / name).is_file()]
    if missing:
        raise ValueError("graph-assets is incomplete; missing: " + ", ".join(missing))
    references = _StaticReferences()
    references.feed((directory / "index.html").read_text(encoding="utf-8"))
    for name in sorted(set(GRAPH_ASSET_FILES) | references.paths):
        asset = (directory / name).resolve()
        if not asset.is_relative_to(directory) or not asset.is_file():
            raise ValueError("graph-assets contains a missing or outside static reference: " + name)
    return directory


def configure_preview_assets(app, directory):
    """Replace only this preview instance's static mount and legacy HTML entry."""
    from fastapi.responses import FileResponse
    from fastapi.routing import APIRoute
    from fastapi.staticfiles import StaticFiles
    from starlette.routing import Mount

    def graph_index():
        return FileResponse(directory / "index.html", headers={"Cache-Control": "no-store"})

    found_static = found_index = False
    for index, route in enumerate(app.router.routes):
        if isinstance(route, Mount) and route.path == "/static":
            route.app = StaticFiles(directory=str(directory))
            found_static = True
        elif isinstance(route, APIRoute) and route.path == "/" and "GET" in route.methods:
            app.router.routes[index] = APIRoute("/", graph_index, methods=["GET"],
                                               include_in_schema=route.include_in_schema)
            found_index = True
    if not found_static:
        app.mount("/static", StaticFiles(directory=str(directory)), name="static")
    if not found_index:
        app.add_api_route("/", graph_index, methods=["GET"], include_in_schema=False)


def graph_fixture_rows(nodes, shape="chain", seed=2026, isolated=False):
    """Deterministic exact-money fixtures; never derive rows from a runtime DB."""
    amounts = (["8888888888888888.88", "0.02", "0.30", "0.40"] if isolated else
               ["9999999999999999.99", "0.01", "0.10", "0.20"])
    rows = []
    for index in range(nodes - 1):
        source = 0 if shape == "hub" else index
        target = index + 1
        rows.append(dict(
            trade_no=f"SYN-GRAPH-{seed}-{index:05d}",
            trade_time=f"2026-01-{index % 28 + 1:02d} 10:{index % 60:02d}:00",
            payer_account=f"000GRAPH-{source:05d}", payer_name=f"合成节点 {source}", payer_bank="合成银行",
            payee_account=f"000GRAPH-{target:05d}", payee_name=f"合成节点 {target}", payee_bank="合成银行",
            amount=Decimal(amounts[(index + seed) % len(amounts)]), balance=None,
            currency="USD" if (index + seed) % 4 == 3 else "CNY",
            summary="合成测试数据 · 非真实交易", channel="synthetic",
            self_account=f"000GRAPH-{source:05d}", self_name=f"合成节点 {source}",
        ))
    return rows


def fixture_summary(case_id, rows, shape, seed):
    totals = {}
    accounts = set()
    for row in rows:
        currency = row["currency"]
        totals[currency] = totals.get(currency, Decimal(0)) + row["amount"]
        accounts.update((row["payer_account"], row["payee_account"]))
    return {"caseId": str(case_id), "nodes": len(accounts), "edges": len(rows), "shape": shape,
            "seed": seed, "totalsByCurrency": {key: format(value, ".2f") for key, value in sorted(totals.items())}}


def register_preview_routes(app, root, metadata, graph_assets=None):
    """Only the disposable preview registers this route, never app.main itself."""
    from fastapi.responses import HTMLResponse

    directory = validate_graph_assets(graph_assets) if graph_assets is not None else root / "static"
    if graph_assets is not None:
        configure_preview_assets(app, directory)

    @app.get("/synthetic/graph-benchmark", include_in_schema=False)
    def graph_benchmark():
        html = (directory / "index.html").read_text(encoding="utf-8")
        encoded = json.dumps(metadata, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
        injection = (f'<script type="application/json" id="graphBenchmarkFixture">{encoded}</script>\n'
                     '<script src="/static/graph-benchmark.js?v=1"></script>\n')
        marker = '<script src="/static/app.js'
        if marker not in html:
            raise RuntimeError("Legacy graph entry is missing; benchmark instrumentation was not installed")
        return HTMLResponse(html.replace(marker, injection + marker, 1), headers={"Cache-Control": "no-store"})


def source_fingerprint(root, graph_assets=None):
    directory = graph_assets if graph_assets is not None else root / "static"
    return {"static/" + path.relative_to(directory).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(directory.rglob("*"))
            if path.is_file() and path.suffix in (".js", ".css", ".html", ".svg")}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0, help="Loopback port; 0 chooses a free port")
    parser.add_argument("--graph-nodes", type=int, default=0, help="Seed two tracked synthetic graphs (2–5000 nodes)")
    parser.add_argument("--graph-shape", choices=("chain", "hub"), default="chain")
    parser.add_argument("--graph-seed", type=int, default=2026, help="Deterministic public fixture seed")
    parser.add_argument("--graph-assets", type=Path, default=None,
                        help="Synthetic preview only: absolute path to a complete comparison static directory")
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("port must be between 0 and 65535")
    if args.graph_nodes and not 2 <= args.graph_nodes <= 5000:
        parser.error("graph-nodes must be between 2 and 5000")
    if not 0 <= args.graph_seed <= 2**31 - 1:
        parser.error("graph-seed must be between 0 and 2147483647")
    if args.graph_assets is not None:
        try:
            args.graph_assets = validate_graph_assets(args.graph_assets)
        except (OSError, UnicodeError, ValueError) as error:
            parser.error(str(error))
    return args


def main():
    args = parse_args()
    root = Path(__file__).resolve().parents[1]
    if not (root / "frontend/dist/index.html").is_file():
        raise SystemExit("Build frontend first: cd frontend && npm ci && npm run build")
    sys.path.insert(0, str(root))
    with tempfile.TemporaryDirectory(prefix="funds-trace-preview-") as temporary:
        directory = Path(temporary)
        # Public fixtures replace inherited settings; never use a business runtime.
        os.environ["FUND_DATA_DIR"] = str(directory / "data")
        os.environ["MPLCONFIGDIR"] = str(directory / "matplotlib")
        os.environ["FUNDS_TRACE_LICENSE_SECRET"] = "synthetic-preview-key-not-for-use"
        os.environ.pop("FUNDS_TRACE_LICENSE_PUBLIC_KEY_FILE", None)
        from app import db, importer, license as licensing
        from app.main import app
        import uvicorn

        db._start_ckpt = lambda: None
        try:
            licensing.save_key(licensing.issue(licensing.machine_code()))
            case_id = db.q("insert into cases(name) values (?) returning id", ["合成演示 · 流水核对"])[0]["id"]
            other_id = db.q("insert into cases(name) values (?) returning id", ["合成演示 · 同账号隔离对照"])[0]["id"]
            empty_id = db.q("insert into cases(name) values (?) returning id", ["合成演示 · 空白案件"])[0]["id"]
            rows = []
            for index, amount in enumerate(["12800.25", "3600.00", "9999999999999999.99"]):
                rows.append(dict(trade_no=f"SYN-DEMO-{index + 1:03d}", trade_time=f"2026-01-0{index + 1} 10:30:00",
                    payer_name="合成甲公司", payer_account="000SYN-A", payer_bank="合成银行",
                    payee_name=f"合成收款方{index + 1}", payee_account=f"000SYN-B{index + 1}", payee_bank="合成银行",
                    amount=Decimal(amount), balance=None, currency="CNY", summary="合成测试数据 · 非真实交易",
                    channel="synthetic", self_account="", self_name=""))
            if args.graph_nodes:
                rows = graph_fixture_rows(args.graph_nodes, args.graph_shape, args.graph_seed)
                other_rows = graph_fixture_rows(args.graph_nodes, args.graph_shape, args.graph_seed, isolated=True)
            else:
                other_rows = [dict(row, amount=Decimal("0.01")) for row in rows]
            importer.commit_rows("synthetic-seed.csv", "A", rows, case_id=case_id)
            importer.commit_rows("synthetic-isolation.csv", "A", other_rows, case_id=other_id)
            if args.graph_nodes:
                for graph_case in (case_id, other_id):
                    db.x("insert into tracked_flows(case_id,flow_id,canvas_id) select case_id,id,0 from flows where case_id=?", [graph_case])
            metadata = {"syntheticOnly": True, "fixtureVersion": "graph-interaction.v1",
                        "cases": [fixture_summary(case_id, rows, args.graph_shape, args.graph_seed),
                                  fixture_summary(other_id, other_rows, args.graph_shape, args.graph_seed),
                                  {"caseId": str(empty_id), "nodes": 0, "edges": 0}],
                        "rendererAssetsOverride": args.graph_assets is not None,
                        "sourceSha256": source_fingerprint(root, args.graph_assets)}
            register_preview_routes(app, root, metadata, graph_assets=args.graph_assets)
            sample = directory / "synthetic-import.csv"
            with sample.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["交易流水号", "交易时间", "付款户名", "付款账号", "收款户名", "收款账号", "交易金额", "币种", "摘要"])
                writer.writerow(["SYN-UPLOAD-001", "2026-01-10 09:00:00", "合成甲公司", "000SYN-A", "合成丁公司", "000SYN-D", "7654.32", "CNY", "合成上传示例"])
                writer.writerow(["SYN-UPLOAD-002", "2026-01-11 09:00:00", "合成丁公司", "000SYN-D", "合成戊公司", "000SYN-E", "100.01", "CNY", "合成上传示例"])
            with socket.socket() as listener:
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                listener.bind(("127.0.0.1", args.port))
                origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
                print(f"Synthetic UI: {origin}/workspace/?case_id={case_id}", flush=True)
                print(f"Synthetic G6: {origin}/?case_id={case_id}", flush=True)
                print(f"Synthetic graph benchmark: {origin}/synthetic/graph-benchmark?case_id={case_id}&label_mode=auto", flush=True)
                print(f"Synthetic all-label baseline: {origin}/synthetic/graph-benchmark?case_id={case_id}&label_mode=all", flush=True)
                print(f"Synthetic CSV: {sample}", flush=True)
                print("Disposable synthetic state only. Ctrl-C stops and removes it.", flush=True)
                uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False)).run(sockets=[listener])
        finally:
            with db._lock:
                if db._con is not None:
                    db._con.close()
                    db._con = None


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
