"""Resource-bounded synthetic storage/query probe, NOT import qualification.

Builds independent synthetic account ledgers directly in a disposable DuckDB.
It does not bypass a public import guard or copy real transactions. Run alone;
the caller separately enforces RSS/disk/time limits and retains the JSON output.
"""
import argparse
import json
import os
from pathlib import Path
import resource
import sys
import tempfile
import time


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows",type=int,required=True)
    args=parser.parse_args()
    if not 100 <= args.rows <= 10_000_000 or args.rows % 100:
        parser.error("rows must be a multiple of 100 between 100 and 10000000")
    root=Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="funds-principal-scale-") as temporary:
        os.environ["FUND_DATA_DIR"]=str(Path(temporary)/"data")
        os.environ["MPLCONFIGDIR"]=str(Path(temporary)/"matplotlib")
        os.environ.pop("FUNDS_TRACE_LICENSE_PUBLIC_KEY_FILE",None)
        os.environ.pop("FUNDS_TRACE_LICENSE_SECRET",None)
        sys.path.insert(0,str(root))
        from app import db
        from app.main import app
        from fastapi.testclient import TestClient
        db._start_ckpt=lambda:None
        connection=db.con()
        connection.execute("set memory_limit='2GB'")
        connection.execute("set threads=2")
        connection.execute("set max_temp_directory_size='8GB'")
        started=time.perf_counter()
        connection.execute("""insert into flows(id,case_id,trade_time,payer_account,payee_account,
            self_account,amount,balance,currency)
            select i+1,1,timestamp '2026-01-01' + (i%100)*interval '1 second',
              case when i%2=0 then 'SYN-OUTSIDE' else 'SYN-L'||cast(i//100 as varchar) end,
              case when i%2=0 then 'SYN-L'||cast(i//100 as varchar) else 'SYN-OUTSIDE' end,
              'SYN-L'||cast(i//100 as varchar),0.01,case when i%2=0 then 0.01 else 0 end,'CNY'
            from range(?) t(i)""",[args.rows])
        connection.execute("checkpoint")
        storage_seconds=time.perf_counter()-started
        payload={"case_id":"1","account":"SYN-L0","currency":"CNY",
                 "start":"2026-01-01T00:00:00","end":"2026-01-02T00:00:00",
                 "source_flow_id":"1","target_flow_ids":["100"],
                 "conditions":{"coverage":"complete_declared","no_overdraft":True,"funds_available":True,
                               "opening_balance":"0.00","balance_kind":"post_book"}}
        timings=[]
        with TestClient(app) as client:
            for _ in range(4):
                started=time.perf_counter()
                response=client.post("/api/analysis/principal",json=payload)
                timings.append(time.perf_counter()-started)
                if response.status_code != 200:
                    raise AssertionError("synthetic query failed")
                value=response.json()
                assert value["status"]=="ok" and value["ledger_count"]==100
                assert (value["targets"][0]["min"],value["targets"][0]["max"],value["targets"][0]["fifo"]) == ("0.00",)*3
            count,total=connection.execute("select count(*),sum(amount) from flows where case_id=1").fetchone()
            assert count==args.rows and str(total)==f"{args.rows//100}.00"
        usage=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        rss_bytes=usage if sys.platform=="darwin" else usage*1024
        disk_bytes=sum(path.stat().st_size for path in Path(temporary).rglob("*") if path.is_file())
        print(json.dumps({"kind":"synthetic_direct_storage_account_query_not_import",
                          "rows":args.rows,"account_rows":100,"storage_seconds":storage_seconds,
                          "first_query_seconds":timings[0],"warm_query_seconds":timings[1:],
                          "peak_rss_bytes":rss_bytes,"runtime_disk_bytes":disk_bytes,
                          "exact_total":str(total),"principal_result":"0.00",
                          "limits":{"duckdb_memory":"2GB","threads":2,"duckdb_temp":"8GB"}},sort_keys=True))
        connection.close(); db._con=None


if __name__=="__main__":
    main()
