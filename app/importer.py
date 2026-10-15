# -*- coding: utf-8 -*-
"""流水文件解析：自动识别表头与列名，规范化为「付款方 → 收款方」双边流水

支持两种常见流水格式：
- 模式 A（双边流水）：同时含付款方、收款方两列（如银联全量、对公双边表）
- 模式 B（单账户流水）：本方账号 + 交易对手 + 收支标志（如个人客户流水）
"""
import datetime
import csv
import decimal
import itertools
import io
import json
import math
import re
from pathlib import Path
from contextlib import contextmanager

import pandas as pd

from . import db

# 解析缓存：同批次流水的时间/金额格式高度一致，命中后单行解析只尝试一种格式
_fmt_cache = {"time": None}
_re_amt_strip = re.compile(r"[^0-9.\-]")
_CENT = decimal.Decimal("0.01")
PARSER_VERSION = "funds-trace-table-v3"


class ImportError(Exception):
    pass


FIELD_SYNONYMS = {
    "trade_no": ["交易流水号", "流水号", "交易单号", "交易号码", "订单号", "凭证号", "凭证编号", "交易序号", "流水编号", "交易编号", "serialno"],
    "trade_time": ["交易日期时间", "交易时间", "交易日期", "业务时间", "业务日期", "记账时间", "记账日期", "转账时间", "转账日期", "发生时间", "发生日期", "交易发生日期", "日期时间", "transdate", "tradedate", "日期", "时间", "date", "time"],
    "payer_name": ["付款人名称", "付款人户名", "付款户名", "付款人", "付方户名", "付方名称", "转出方名称", "转出方户名", "出账方名称", "借方户名", "付款方名称", "付款方"],
    "payer_account": ["付款人账号", "付款人卡号", "付款账号", "付款卡号", "付方账号", "转出账号", "转出卡号", "出账账号", "借方账号", "付款方账号"],
    "payer_bank": ["付款人开户行", "付款开户行", "付款行", "转出开户行", "付方开户行"],
    "payee_name": ["收款人名称", "收款人户名", "收款户名", "收款人", "收方户名", "收方名称", "转入方名称", "转入方户名", "入账方名称", "贷方户名", "收款方名称", "收款方"],
    "payee_account": ["收款人账号", "收款人卡号", "收款账号", "收款卡号", "收方账号", "转入账号", "转入卡号", "入账账号", "贷方账号", "收款方账号"],
    "payee_bank": ["收款人开户行", "收款开户行", "收款行", "转入开户行", "收方开户行"],
    "amount": ["交易金额", "转账金额", "发生金额", "交易额", "本方交易金额", "金额", "amount"],
    "balance": ["交易后余额", "账户余额", "余额", "balance"],
    "currency": ["交易币种", "币种", "货币", "货币种类", "currency", "钞汇标志"],
    "summary": ["交易摘要", "用途及附言", "摘要/附言", "摘要", "附言", "用途", "备注", "交易附言", "说明", "remark"],
    "channel": ["交易渠道/方式", "交易渠道", "交易方式", "业务类型", "交易类型", "渠道"],
    # —— 单账户流水（模式 B）——
    "self_account": ["本方账号", "交易账号", "客户账号", "本方卡号", "账号", "卡号", "账户"],
    "self_name": ["本方户名", "本方名称", "账户名称", "客户名称", "户名"],
    "counter_name": ["交易对方户名", "对手户名", "对方户名", "交易对手方名称", "对方名称", "交易对方名称", "对方账户名称", "对方客户名称", "交易对方", "对手方名称", "对手名称", "交易对手"],
    "counter_account": ["交易对手账卡号", "对手账卡号", "对方账号", "对方账户", "交易对方账号", "对方卡号", "对手方账号", "交易对手账号", "对手账号", "对手卡号"],
    "counter_bank": ["对方开户行", "对方行名", "对方银行", "交易对方开户行"],
    "direction": ["收付标志", "收支标志", "借贷标志", "借方/贷方", "交易方向", "资金方向", "收/支", "收/付", "收支", "借贷", "direction"],
    "income_amt": ["贷方发生额", "收入金额", "收入发生额", "转入金额", "收方发生额", "贷方金额"],
    "expense_amt": ["借方发生额", "支出金额", "支出发生额", "转出金额", "付方发生额", "借方金额"],
}

# 模式 B 中，带收支标志的流水常把收/付款人列当作交易对手
B_EXTRA = {
    "counter_name": ["收款人名称", "付款人名称", "收款人", "付款人", "收款户名", "付款户名"],
    "counter_account": ["收款人账号", "付款人账号", "收款账号", "付款账号", "收款人卡号", "付款人卡号"],
}

MODE_FIELDS = {
    "A": ["trade_no", "trade_time", "payer_name", "payer_account", "payer_bank",
          "payee_name", "payee_account", "payee_bank", "self_account", "amount", "balance",
          "currency", "summary", "channel"],
    "B": ["trade_no", "trade_time", "counter_name", "counter_account", "counter_bank",
          "direction", "income_amt", "expense_amt", "self_name", "self_account",
          "amount", "balance", "currency", "summary", "channel"],
}

HEADER_KEYS = ["账号", "户名", "金额", "时间", "日期", "余额", "摘要", "对方", "收款", "付款", "流水", "收支", "标志", "借贷"]

TIME_FMTS = [
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d", "%Y-%m-%d%H:%M:%S", "%Y%m%d %H%M%S", "%Y%m%d%H%M%S", "%Y%m%d",
]

_OUT_EXACT = {"d", "借", "付", "支", "出", "out"}
_IN_EXACT = {"c", "贷", "收", "入", "进", "存", "in"}
_OUT_SUB = ["支出", "转出", "借方", "付方", "付款", "借记", "取现", "取款", "付出", "出账", "支取", "提现", "debit", "exp"]
_IN_SUB = ["收入", "转入", "贷方", "收方", "收款", "存入", "贷记", "入账", "进账", "存现", "credit", "income"]


def _norm(h):
    return str(h).replace("\u3000", "").replace(" ", "").replace("　", "").strip().lower()


def _tkey(t):
    if isinstance(t, datetime.datetime):
        return t.strftime("%Y-%m-%d %H:%M:%S")
    return t


def match_columns(cols, mode):
    """按字段优先级 + 同义词匹配列名，返回 {字段: 列名}"""
    syns = {f: list(FIELD_SYNONYMS.get(f, [])) for f in MODE_FIELDS[mode]}
    if mode == "A":
        # 双边流水不自动猜测"本方账号"列（"账号"等同义词过于宽泛，易误标主账号）；
        # 映射界面保留该字段供用户手动指定
        syns["self_account"] = []
    for f, extra in B_EXTRA.items():
        if f in syns:
            syns[f] = extra + syns[f]
    mapping, used = {}, set()
    # 第一轮：整名精确匹配（字段顺序即优先级）
    for f in MODE_FIELDS[mode]:
        for s in syns[f]:
            hit = next((c for c in cols if c not in used and _norm(c) == s), None)
            if hit:
                mapping[f] = hit
                used.add(hit)
                break
    # 第二轮：包含匹配，同义词长的优先
    for f in MODE_FIELDS[mode]:
        if f in mapping:
            continue
        for s in sorted(syns[f], key=len, reverse=True):
            hit = next((c for c in cols if c not in used and s in _norm(c)), None)
            if hit:
                mapping[f] = hit
                used.add(hit)
                break
    return mapping


def _validate(mapping, mode):
    if not mapping.get("trade_time"):
        return False
    if not (mapping.get("amount") or (mapping.get("income_amt") and mapping.get("expense_amt"))):
        return False
    if mode == "A":
        return bool((mapping.get("payer_account") or mapping.get("payer_name"))
                    and (mapping.get("payee_account") or mapping.get("payee_name")))
    return bool(mapping.get("self_account")
                and (mapping.get("counter_account") or mapping.get("counter_name"))
                and (mapping.get("direction") or (mapping.get("income_amt") and mapping.get("expense_amt"))))


_TIME_ONLY_RE = re.compile(r"\d{1,2}:\d{2}(:\d{2})?$")
_DATE_ONLY_RE = re.compile(r"(\d{4}[-/.年]\d{1,2}([-/.月]\d{1,2}日?)?|\d{8})$")


def _col_kinds(df, c, mapping):
    """取某列前 30 个非空值"""
    if c in mapping.values():
        return None
    return [str(v).strip() for v in df[c].dropna().head(30).tolist()
            if str(v).strip() and str(v).lower() != "nan"]


def detect_time_extra(df, mapping):
    """日期与时分分成两列时找到互补列：
    trade_time 只有日期 → 找纯时分列；trade_time 只有时分 → 找纯日期列"""
    tc = mapping.get("trade_time")
    if not tc:
        return None
    vals = [str(v).strip() for v in df[tc].dropna().head(30).tolist()
            if str(v).strip() and str(v).lower() != "nan"]
    if not vals:
        return None
    has_colon = any(":" in v for v in vals)
    has_date = any(_DATE_ONLY_RE.search(v) and not _TIME_ONLY_RE.search(v) for v in vals)
    # 列值以日期开头（如 2026-09-18 17:39:00）→ 已含完整日期时间，不再拼接日期列
    date_at_start = any(re.match(r"^(\d{4}[-/.年]\d{1,2}|\d{8})", v) for v in vals)
    if has_colon and date_at_start:
        return None  # 本列已是完整日期时间
    for c in df.columns:
        if c == tc:
            continue
        cv = _col_kinds(df, c, mapping)
        if not cv:
            continue
        if has_date and not has_colon:
            if all(_TIME_ONLY_RE.fullmatch(v) for v in cv):
                return c
        if has_colon and not has_date:
            if all(_DATE_ONLY_RE.fullmatch(v) for v in cv):
                return c
    return None


def analyze(df, mode_hint=None):
    """识别流水模式与列映射"""
    cols = [c for c in df.columns]
    for mode in (["A", "B"] if not mode_hint else [mode_hint]):
        m = match_columns(cols, mode)
        if _validate(m, mode):
            extra = detect_time_extra(df, m)
            if extra:
                m["_time2"] = extra
            return mode, m
    raise ImportError(
        "无法识别列结构：需包含「交易时间 + 金额 + 付款/收款双方」（双边流水），"
        "或「本方账号 + 对手信息 + 收支标志」（单账户流水）。请在预览中手动指定列映射。")


def find_header_row(raw):
    """在含标题行的报表中定位真正的表头行"""
    best, best_score = None, 0
    for i in range(min(20, len(raw))):
        vals = [str(v).strip() for v in raw.iloc[i].tolist() if str(v).strip() and str(v) != "nan"]
        score = sum(1 for v in vals if any(k in _norm(v) for k in HEADER_KEYS))
        if score >= 3 and score > best_score:
            best, best_score = i, score
    return best


def _tidy(df):
    df = df.dropna(how="all").dropna(axis=1, how="all")
    df.columns = [str(c).strip() for c in df.columns]
    return df


def _rewind(source):
    if hasattr(source, "seek"):
        source.seek(0)
    return source


@contextmanager
def _csv_text(source, encoding):
    if hasattr(source, "read"):
        text = io.TextIOWrapper(_rewind(source), encoding=encoding, newline="")
        try:
            yield text
        finally:
            text.detach()
    else:
        with Path(source).open("r", encoding=encoding, newline="") as text:
            yield text


def _source_size(source):
    if not hasattr(source, "read"):
        return Path(source).stat().st_size
    position = source.tell()
    source.seek(0, 2)
    size = source.tell()
    source.seek(position)
    return size


def _read_detect(path, sep, enc):
    """表头探测读取。names 预置 100 列使短行补 NaN——避免以标题行宽度为准导致的 ParserError"""
    if sep is None:
        return pd.read_csv(_rewind(path), sep=None, engine="python", header=None,
                           names=range(100), dtype=str, nrows=30, encoding=enc,
                           skip_blank_lines=False)
    return pd.read_csv(_rewind(path), sep=sep, header=None, names=range(100),
                       dtype=str, nrows=30, encoding=enc, skip_blank_lines=False)


def _read_full(path, sep, hdr, enc):
    """正式读取：以表头行为列，标题行已被跳过"""
    if sep is None:
        frame = pd.read_csv(_rewind(path), sep=None, engine="python", header=hdr, dtype=str,
                            encoding=enc, skip_blank_lines=False)
    else:
        frame = pd.read_csv(_rewind(path), sep=sep, header=hdr, dtype=str, encoding=enc,
                            on_bad_lines="warn", skip_blank_lines=False)
    # csv.reader counts logical records, including an empty record. A quoted
    # multiline value is one record; these numbers deliberately are not physical
    # text-line numbers. Keep malformed wide records out exactly as pandas does.
    # The stdlib default (128 KiB per field) is below pandas' accepted field
    # size. Tracking must not reject a file pandas has already parsed.
    csv.field_size_limit(max(csv.field_size_limit(), _source_size(path)))
    with _csv_text(path, enc) as source:
        if sep is None:
            sample = source.read(65536)
            source.seek(0)
            delimiter = csv.Sniffer().sniff(sample).delimiter
        else:
            delimiter = sep
        records = csv.reader(source, delimiter=delimiter)
        header = next(itertools.islice(records, hdr, hdr + 1), None)
        if header is None:
            raise ImportError("无法定位 CSV 表头的来源记录")
        positions = [number for number, record in enumerate(records, start=hdr + 2)
                     if len(record) <= len(header)]
    if len(positions) != len(frame):
        raise ImportError("CSV 源记录位置与解析结果不一致，请规范文件列结构后重试")
    frame.index = positions
    frame.attrs.update(source_row_kind="csv_record", source_sheet="")
    return frame


def read_id_table(path: Path):
    """读取账号-身份证对照表（非流水表，不走流水表头嗅探）。

    自动定位「身份证/证件」与「账号类」同现的表头行；返回 [(账号, 证件号)]，
    全程按文本读取，避免长账号被 Excel 转成科学计数法丢精度。
    """
    suffix = path.suffix.lower()
    sheets = []
    if suffix in (".xlsx", ".xls"):
        xs = pd.ExcelFile(path)
        for sh in xs.sheet_names:
            try:
                sheets.append(xs.parse(sh, header=None, dtype=str))
            except Exception:
                continue
    else:
        last_err = None
        for enc in ("utf-8-sig", "gbk", "gb18030", "utf-8", "utf-16"):
            try:
                sheets = [pd.read_csv(path, header=None, dtype=str, encoding=enc,
                                      sep=None, engine="python")]
                break
            except Exception as e:
                last_err = e
        if not sheets:
            raise ImportError(f"CSV 解析失败（{last_err}）")

    def _cell(v):
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return ""
        return str(v).strip()

    for raw in sheets:
        if raw is None or raw.empty or raw.shape[1] < 2:
            continue
        hdr_idx = None
        for i in range(min(len(raw), 30)):
            joined = "".join(_cell(v) for v in raw.iloc[i].tolist())
            if (("身份" in joined or "证件" in joined)
                    and any(w in joined for w in ("账号", "帐号", "卡号", "账户"))):
                hdr_idx = i
                break
        if hdr_idx is None:
            continue
        header = [_cell(v) for v in raw.iloc[hdr_idx].tolist()]
        data = raw.iloc[hdr_idx + 1:].copy()
        data.columns = [h if h else f"col{j}" for j, h in enumerate(header)]
        id_col = next((c for c in data.columns if ("身份" in c) or ("证件" in c)), None)
        acct_col = next((c for c in data.columns
                         if ("身份" not in c and "证件" not in c
                             and any(w in c for w in ("账号", "帐号", "卡号", "账户")))), None)
        if id_col is None or acct_col is None:
            continue
        out = []
        for _, r in data.iterrows():
            out.append((_cell(r[acct_col]), _cell(r[id_col])))
        return out
    raise ImportError("未找到包含「账号」与「身份证/证件号」的表头行")


def read_table(path, *, suffix=None):
    """读取 CSV / Excel，自动定位表头。返回 (DataFrame, sheet名)

    CSV 依次尝试多种编码 × 分隔符组合，以「表头行得分」为校验：
    带标题行的报表若用自动嗅探，常被标题行中的空格误导，故显式分隔符优先。
    """
    suffix = (suffix if suffix is not None else Path(path).suffix).lower()
    if suffix in (".xlsx", ".xls"):
        best = None
        with pd.ExcelFile(_rewind(path)) as xs:
            for sh in xs.sheet_names:
                try:
                    raw = xs.parse(sh, header=None, dtype=str, nrows=30)
                except Exception:
                    continue
                hdr = find_header_row(raw)
                if hdr is None:
                    continue
                df = xs.parse(sh, header=hdr, dtype=str)
                df.index = df.index + hdr + 2
                df.attrs.update(source_row_kind="worksheet_row", source_sheet=sh)
                df = _tidy(df)
                score = len(match_columns(list(df.columns), "A")) + len(match_columns(list(df.columns), "B"))
                if best is None or score > best[0]:
                    best = (score, df, sh)
                if score >= 10:
                    break
            if best is None:
                raise ImportError("Excel 中找不到有效表头行（需包含账号/金额/时间等列名）")
            return best[1], best[2]
    err = None
    for enc in ("utf-8-sig", "gbk", "gb18030", "utf-8", "utf-16"):
        for sep in (",", "\t", ";", "|", None):
            try:
                raw = _read_detect(path, sep, enc)
                hdr = find_header_row(raw)
                if hdr is None:
                    continue
                df = _tidy(_read_full(path, sep, hdr, enc))
                if len(df.columns) >= 3:
                    return df, ""
            except ValueError as e:  # 含 UnicodeDecodeError 与 pandas ParserError
                err = e
                if isinstance(e, UnicodeDecodeError):
                    break  # 该编码无法解码，跳过其余分隔符
    raise ImportError(f"CSV 解析失败（{err}）：请转换为 UTF-8 或 GBK 编码")


def parse_time(v):
    if v is None:
        return None
    if isinstance(v, (pd.Timestamp, datetime.datetime, datetime.date)):
        return pd.Timestamp(v).to_pydatetime()
    s = str(v).strip()
    if not s or s.lower() in ("nan", "none", "nat"):
        return None
    s = s.replace("年", "-").replace("月", "-").replace("日", "").replace("/", "-").strip()
    m = re.match(r"^(\d{4})\.(\d{1,2})\.(\d{1,2})", s)
    if m:
        s = s[:m.end()].replace(".", "-") + s[m.end():]
    # 优先使用上次成功的格式（同批次格式一致，避免每行试遍全部格式）
    hit = _fmt_cache["time"]
    if hit:
        try:
            return datetime.datetime.strptime(s, hit)
        except ValueError:
            pass
    for f in TIME_FMTS:
        try:
            r = datetime.datetime.strptime(s, f)
            _fmt_cache["time"] = f
            return r
        except ValueError:
            pass
    t = pd.to_datetime(s, errors="coerce")
    if not pd.isna(t):
        return t.to_pydatetime()
    # Excel 日期序列号
    try:
        fv = float(_re_amt_strip.sub("", s))
        if 20000 < fv < 80000:
            return datetime.datetime(1899, 12, 30) + datetime.timedelta(days=fv)
    except ValueError:
        pass
    return None


def parse_amount(v):
    if v is None:
        return None
    s = str(v).strip()
    if not s or s.lower() in ("nan", "none", "null", "--", "-"):
        return None
    neg = s.startswith("(") and s.endswith(")")
    if neg:
        s = s[1:-1].strip()
    s = s.replace(",", "").replace("，", "").replace("￥", "").replace("¥", "").replace(" ", "")
    # Parse the whole value: deleting unknown characters changed 1e3 into 13.
    if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", s):
        return None
    try:
        d = decimal.Decimal(s)
    except decimal.InvalidOperation:
        return None
    if not d.is_finite():
        return None
    # 金额按「分」四舍五入（入库字段为 DECIMAL(18,2)），避免浮点尾差
    try:
        d = d.quantize(_CENT, rounding=decimal.ROUND_HALF_UP)
    except decimal.InvalidOperation:
        return None
    return -d if neg else d


def parse_direction(v):
    s = _norm(v)
    if not s or s in ("nan", "none"):
        return None
    if s in _OUT_EXACT:
        return "out"
    if s in _IN_EXACT:
        return "in"
    for w in _OUT_SUB:
        if w in s:
            return "out"
    for w in _IN_SUB:
        if w in s:
            return "in"
    return None


def _infer_time_format(samples):
    """从样本值推断时间格式（strptime 格式可直接用于 to_datetime 的 C 快路径）"""
    for s in samples:
        if not s:
            continue
        for f in TIME_FMTS:
            try:
                datetime.datetime.strptime(s, f)
                return f
            except ValueError:
                pass
    return None


def _clean_str(v):
    if v is None:
        return ""
    if isinstance(v, float) and math.isnan(v):
        return ""
    s = str(v).strip()
    return "" if s.lower() in ("nan", "none", "nat") else s


def build_rows(df, mode, mapping=None):
    """按映射把原始行规范化为双边流水 dict 列表。
    时间列整列向量化解析（快 10 倍以上），奇怪格式逐行兜底。"""
    if mode not in MODE_FIELDS:
        raise ImportError(f"未知流水模式 {mode}")
    mapping = {k: v for k, v in (mapping or {}).items() if v}
    n = len(df)
    if not n:
        return [], {"total": 0, "ok": 0, "skipped": 0, "bad_examples": []}

    def col_list(f):
        c = mapping.get(f)
        if not c or c not in df.columns:
            return [""] * n
        return [_clean_str(v) for v in df[c].tolist()]

    # ---- 时间列：合并（含独立时分列）→ 推断格式整列解析 → 失败行逐行兜底 ----
    t_raw = col_list("trade_time")
    t2_raw = col_list("_time2")
    merged = []
    for i in range(n):
        tstr = t_raw[i]
        if t2_raw and t2_raw[i]:
            a, b = tstr, t2_raw[i]
            if _TIME_ONLY_RE.fullmatch(a) and _DATE_ONLY_RE.fullmatch(b):
                a, b = b, a
            tstr = f"{a} {b}".strip()
        merged.append(tstr)
    fmt = _infer_time_format([m for m in merged[:50] if m])
    if fmt:
        # 带 format 走 pandas C 快路径（整列一次解析）
        ts = pd.to_datetime(pd.Series(merged), format=fmt, errors="coerce")
    else:
        ts = pd.Series([pd.NaT] * n, dtype="datetime64[ns]")
    # NaT 行逐行兜底（重建 Series，避免逐行 setitem 的开销）
    ts_vals = ts.tolist()
    nat_fixed = False
    for i, v in enumerate(ts_vals):
        if v is None or pd.isna(v):
            t = parse_time(merged[i]) if merged[i] else None
            ts_vals[i] = pd.Timestamp(t) if t else pd.NaT
            nat_fixed = True
    if nat_fixed:
        ts = pd.Series(ts_vals)

    # ---- 金额与收支（轻量逐行，含货币符号/括号负数边角） ----
    amt_raw = [parse_amount(v) for v in col_list("amount")]
    if mode == "B":
        d_raw = col_list("direction")
        inc = [parse_amount(v) for v in col_list("income_amt")]
        exp = [parse_amount(v) for v in col_list("expense_amt")]

    # ---- 各字段列预提取，按行解包为字典（行内 O(1) 取值） ----
    cols = {f: col_list(f) for f in set(sum([MODE_FIELDS[mode], ["_time2", "self_account", "self_name"]], []))}
    fieldnames = list(dict.fromkeys(sum([MODE_FIELDS[mode], ["_time2", "self_account", "self_name"]], [])))
    col_lists = [cols.get(f, [""] * n) for f in fieldnames]
    row_dicts = (dict(zip(fieldnames, vals)) for vals in zip(*col_lists))

    source_rows = list(df.index) if df.attrs.get("source_row_kind") in ("csv_record", "worksheet_row") else None
    source_sheet = df.attrs.get("source_sheet", "")
    rows, skipped, examples = [], 0, []

    for i, rec in enumerate(row_dicts):
        tt = ts_vals[i]
        tt = None if tt is None or pd.isna(tt) else tt.to_pydatetime()
        amount = amt_raw[i]
        direction = None

        if mode == "B":
            d = parse_direction(d_raw[i])
            inc_v, exp_v = inc[i], exp[i]
            if d:
                direction = d
            elif inc_v:
                direction = "in"
            elif exp_v:
                direction = "out"
            if amount is None:
                amount = inc_v or exp_v

        reason = None
        if tt is None:
            reason = "交易时间无法解析"
        elif amount is None or amount <= 0:
            reason = "金额无效"
        if reason:
            skipped += 1
            if len(examples) < 3:
                examples.append({"row": merged[i] or "(空)", "reason": reason,
                                 **({"source_row": int(source_rows[i])} if source_rows is not None else {})})
            continue

        if mode == "A":
            payer_name, payer_account = rec["payer_name"], rec["payer_account"]
            payer_bank = rec["payer_bank"]
            payee_name, payee_account = rec["payee_name"], rec["payee_account"]
            payee_bank = rec["payee_bank"]
        else:
            if direction == "in":  # 收入：对手付给本方
                payer_name, payer_account, payer_bank = rec["counter_name"], rec["counter_account"], rec["counter_bank"]
                payee_name, payee_account, payee_bank = rec["self_name"], rec["self_account"], ""
            else:  # 支出：本方付给对手
                payer_name, payer_account, payer_bank = rec["self_name"], rec["self_account"], ""
                payee_name, payee_account, payee_bank = rec["counter_name"], rec["counter_account"], rec["counter_bank"]

        if not payer_account:
            payer_account = payer_name
        if not payee_account:
            payee_account = payee_name
        if not payer_account or not payee_account:
            skipped += 1
            if len(examples) < 3:
                examples.append({"row": merged[i] or "(空)", "reason": "缺少付款/收款账户",
                                 **({"source_row": int(source_rows[i])} if source_rows is not None else {})})
            continue

        rows.append({
            "trade_no": rec["trade_no"],
            "trade_time": tt,
            "payer_name": payer_name, "payer_account": payer_account, "payer_bank": payer_bank,
            "payee_name": payee_name, "payee_account": payee_account, "payee_bank": payee_bank,
            "amount": abs(amount),
            "balance": parse_amount(rec["balance"]),
            "currency": (_norm(rec["currency"]).upper() or None) if rec.get("currency") else None,
            "summary": rec["summary"],
            "channel": rec["channel"],
            # 导入时声明的本方账号（映射了"本方账号"列即用户声明：该账号为主账号）
            "self_account": rec["self_account"],
            "self_name": rec["self_name"],
            **({"_source_row": int(source_rows[i]), "_source_sheet": source_sheet}
               if source_rows is not None else {}),
            **({"_source_row_kind": df.attrs["source_row_kind"]} if source_rows is not None else {}),
        })

    stats = {"total": int(len(df)), "ok": len(rows), "skipped": skipped, "bad_examples": examples}
    return rows, stats


def _row_key(r, default_currency=""):
    """全列指纹：所有导入列完全一致才视为同一笔（重复）流水"""
    bal = r.get("balance")
    return (r.get("trade_no") or "", _tkey(r.get("trade_time")),
            r.get("payer_name") or "", r.get("payer_account") or "", r.get("payer_bank") or "",
            r.get("payee_name") or "", r.get("payee_account") or "", r.get("payee_bank") or "",
            decimal.Decimal(str(r.get("amount") or 0)).quantize(_CENT),
            decimal.Decimal(str(bal)).quantize(_CENT) if bal is not None else None,
            r.get("currency") or default_currency, r.get("summary") or "", r.get("channel") or "")


def _provenance_metadata(provenance, filename, mode, rows):
    """Validate trusted parser metadata before any persistent write."""
    source = dict(provenance)
    source.setdefault("row_kind", rows[0].get("_source_row_kind") if rows else "unknown")
    mapping = source.get("mapping")
    suffix = Path(filename).suffix.lower() if isinstance(filename, str) else ""
    if (not isinstance(source.get("source_id"), str)
            or not re.fullmatch(r"[0-9a-f]{12,32}", source["source_id"])
            or not isinstance(source.get("sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", source["sha256"])
            or type(source.get("size_bytes")) is not int or source["size_bytes"] < 0
            or source.get("mode") != mode or mode not in MODE_FIELDS
            or source.get("filename") != filename or not isinstance(filename, str) or not filename
            or not isinstance(source.get("sheet"), str)
            or source.get("row_kind") not in ("csv_record", "worksheet_row", "unknown")
            or not isinstance(source.get("parser_version"), str) or not source["parser_version"]
            or not isinstance(mapping, dict)
            or any(k not in (*MODE_FIELDS.get(mode, []), "_time2", "self_account", "self_name")
                   or not isinstance(v, str) or not v.strip() for k, v in mapping.items())
            or not _validate(mapping, mode)
            or suffix not in (".csv", ".tsv", ".txt", ".xlsx", ".xls")
            or (suffix in (".xlsx", ".xls")) != bool(source.get("sheet"))
            or (source["row_kind"] == "worksheet_row" and suffix not in (".xlsx", ".xls"))
            or (source["row_kind"] == "csv_record" and suffix in (".xlsx", ".xls"))):
        raise ImportError("来源元数据无效")
    positions = set()
    for row in rows:
        position = row.get("_source_row")
        if (type(position) is not int or position <= 0 or position in positions
                or row.get("_source_sheet") != source["sheet"]
                or row.get("_source_row_kind") != source["row_kind"]):
            raise ImportError("来源行号或工作表无效")
        positions.add(position)
    return source


def commit_rows(filename, mode, rows, stats=None, progress_cb=None, case_id=1, provenance=None):
    """去重后分批入库（去重范围为当前案件内），progress_cb(done, total) 汇报进度。
    去重规则：所有导入列完全一致才判为重复——任一列（如余额、摘要、对手）不同均视为不同流水。"""
    source = _provenance_metadata(provenance, filename, mode, rows) if provenance is not None else None
    with db.transaction() as con:
        if not db.q("select id from cases where id = ?", [case_id]):
            raise ImportError("案件不存在，无法导入流水")
        key_cols = ("trade_no, trade_time, payer_name, payer_account, payer_bank, "
                    "payee_name, payee_account, payee_bank, amount, balance, currency, summary, channel")
        existing = {}
        cursor = con.execute(f"select id,{key_cols} from flows where case_id = ? order by id", [case_id])
        names = [column[0] for column in cursor.description]
        for values in cursor.fetchall():
            record = dict(zip(names, values))
            existing.setdefault(_row_key(record), record["id"])

        seen, final, references, dup = {}, [], [], 0
        for r in rows:
            # Match the value insertion will persist, without reinterpreting
            # genuinely unknown currency values in an existing database.
            k = _row_key(r, default_currency="CNY")
            if k in existing or k in seen:
                dup += 1
                if source:
                    references.append((r["_source_row"], k, "duplicate"))
                continue
            seen[k] = len(final)
            final.append(r)
            if source:
                references.append((r["_source_row"], k, "imported"))

        # Allocate IDs in one query so provenance can refer to the exact rows
        # inserted, including duplicates later in the same source file.
        flow_ids = [record[0] for record in con.execute(
            "select nextval('flow_seq') from range(?)", [len(final)]).fetchall()]
        final_ids = {key: flow_ids[index] for key, index in seen.items()}
        if source:
            con.execute("insert into import_sources(source_id,case_id,filename,sha256,size_bytes,mode,mapping_json,sheet,parser_version,row_kind,origin,imported_at) values (?,?,?,?,?,?,?,?,?,?,'upload',current_timestamp at time zone 'UTC')",
                        [source["source_id"], case_id, filename, source["sha256"], source["size_bytes"], mode,
                         json.dumps(source["mapping"], ensure_ascii=False, sort_keys=True), source["sheet"], source["parser_version"], source["row_kind"]])

        batch_id = db.q("select nextval('batch_seq') id")[0]["id"]
        db.x("insert into batches(id, case_id, filename, mode, rows_total, rows_imported, rows_skipped, rows_dup) values (?,?,?,?,?,?,?,?)",
             [batch_id, case_id, filename, mode,
              (stats or {}).get("total", len(rows)), len(final), (stats or {}).get("skipped", 0), dup])
        if final:
            # 批量插入，保持整批事务边界。
            cols = ["id", "case_id", "batch_id", "trade_no", "trade_time", "payer_name", "payer_account", "payer_bank",
                    "payee_name", "payee_account", "payee_bank", "amount", "balance", "currency", "summary", "channel",
                    "self_account", "self_name"]
            data = [[fid, case_id, batch_id, r["trade_no"], r["trade_time"], r["payer_name"], r["payer_account"], r.get("payer_bank") or "",
                     r["payee_name"], r["payee_account"], r.get("payee_bank") or "",
                     r["amount"], r.get("balance"), r.get("currency") or "CNY", r.get("summary") or "", r.get("channel") or "",
                     r.get("self_account") or "", r.get("self_name") or ""] for fid, r in zip(flow_ids, final)]
            df_ins = pd.DataFrame(data, columns=cols)
            df_ins["trade_time"] = pd.to_datetime(df_ins["trade_time"])
            # DuckDB samples pandas object columns and may infer a narrow
            # DECIMAL before encountering a rare larger value. A nullable text
            # bridge preserves every Decimal digit; the target DECIMAL(18,2)
            # columns perform the final exact cast and range validation.
            for field in ("amount", "balance"):
                df_ins[field] = df_ins[field].astype("string")
            CHUNK = 50000
            done = 0
            for i in range(0, len(df_ins), CHUNK):
                db.set_busy(True)
                try:
                    db.insert_dataframe("flows", df_ins.iloc[i:i + CHUNK], cols)
                finally:
                    db.set_busy(False)
                done = min(i + CHUNK, len(df_ins))
                if progress_cb:
                    progress_cb(done, len(df_ins))
        if references:
            reference_frame = pd.DataFrame(
                [[case_id, source["source_id"], position,
                  existing.get(key, final_ids.get(key)), disposition]
                 for position, key, disposition in references],
                columns=["case_id", "source_id", "source_row", "flow_id", "disposition"])
            for offset in range(0, len(reference_frame), 50000):
                db.insert_dataframe("flow_sources", reference_frame.iloc[offset:offset + 50000],
                                    list(reference_frame.columns))
        st = dict(stats or {})
        st.update({"ok": len(final), "rows_dup": dup, "batch_id": batch_id})
        # 导入时声明的本方账号（"本方账号"列中出现过的账号）→ 主账号候选。
        # 本方账号列（交易账号）出现的全部账号均视为本次导出的本方账号，默认全部标记；
        # 导入预览界面会列出清单供勾选确认，可取消非主账号。
        # 注意：本方账号声明是"文件级"属性——即使全部行因去重被跳过，
        # 文件声明的本方账号仍应标记（重复导入时标记保持/确认，不丢失）
        self_map = {}
        for r in rows:
            sa = (r.get("self_account") or "").strip()
            if sa:
                self_map.setdefault(sa, (r.get("self_name") or "").strip())
        st["self_accounts"] = [{"account": a, "name": n} for a, n in self_map.items()]
        st["self_accounts_count"] = len(self_map)
        # 主账号匹配：本批次中出现、且已被标记为主账号的账号数（标记跟随账号，自动保持）
        batch_accts = set()
        for r in final:
            if r.get("payer_account"):
                batch_accts.add(r["payer_account"])
            if r.get("payee_account"):
                batch_accts.add(r["payee_account"])
        if batch_accts:
            mains = {r["account"] for r in db.q("select account from main_accounts where case_id = ?", [case_id])}
            st["main_matched"] = sorted(batch_accts & mains)
            st["accounts_in_batch"] = len(batch_accts)
        return st
