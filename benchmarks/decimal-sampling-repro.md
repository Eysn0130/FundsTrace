# Decimal 扫描精度的合成复现

2026-10-15 UTC 的百万行容量观察在候选指纹 `19c429bdaed92617179d68811ffa1719fe05983dcdc3b99460b824431d6bb8bf` 的 `commit_atomic` 阶段失败，未生成容量成功报告。调用位置为 `app/importer.py` 的 50,000 行分块插入及 `app/db.py` 的 `insert_dataframe` 注册/插入边界。主代理随后负责显式精确类型桥接与业务回归；下面是定位根因的原生 DuckDB/pandas 最小合成诊断，应用修复后它仍可能展示上游采样行为。

实际环境为 DuckDB 1.5.6、pandas 3.0.6；`pandas_analyze_sample=1000`。金额与余额的 DataFrame dtype 都是 `object`，非空值的 Python 类型均为 `Decimal`。2,000 行案例中，扫描关系被推断为 `amount DECIMAL(6,2)`、`balance DECIMAL(8,2)`，即使目标表金额为 `DECIMAL(18,2)`，扫描输入阶段已溢出：

```text
InvalidInputException: Invalid Input Error: Failed to cast value: Casting value "10000.00" to type DECIMAL(6,2) failed: value is out of range!
```

以下代码只使用内存库和临时环境，不读取现有应用数据库：

```python
from decimal import Decimal
import os
from pathlib import Path
import tempfile

with tempfile.TemporaryDirectory(prefix="funds-trace-decimal-probe-") as temporary:
    os.environ["FUND_DATA_DIR"] = str(Path(temporary) / "data")
    os.environ["MPLCONFIGDIR"] = str(Path(temporary) / "matplotlib")
    os.environ["FUNDS_TRACE_LICENSE_SECRET"] = "synthetic-benchmark-key-not-for-use"
    import duckdb
    import pandas as pd

    frame = pd.DataFrame({
        "amount": [Decimal("9999.99")] * 1999 + [Decimal("10000.00")],
        "balance": [None] + [Decimal("500000.00")] * 1999,
    })
    connection = duckdb.connect(":memory:")
    try:
        connection.register("_ins_df", frame)
        print(frame.dtypes)
        print(connection.execute("describe select * from _ins_df").fetchall())
        connection.execute("create table target(amount decimal(18,2), balance decimal(18,2))")
        connection.execute("insert into target select amount, balance from _ins_df")
    finally:
        connection.close()
```

同样布局的 1,999 行对照（最后一行为稀有较大值）会被推断成 `DECIMAL(7,2)` 并成功，说明行数和采样落点会改变结果。50,000 行中将较大值放在偏移 3,916 也可复现。固定 seed `20261014` 的百万行夹具恰在源行索引 515,815 产生 `10000.00`；去除无效/重复行后位于唯一流水偏移 453,916，即第 10 个 50,000 行块的偏移 3,916。金额范围不应因这次失败而缩小。
