"""Check monetary source citations for the isolated, read-only original review."""
from .. import table_import
from . import import_balance_trial


QUESTION = (
    "从当前原表核对哪一列是原报交易金额、哪一列是原报余额、哪里记录来源币种。"
    "金额和余额分别判断本列明示的缩放单位。兼容基础单位不是原件明示，不能从邻列借单位。"
    "币种原码保持原报含义，没有码表时不翻译。引用一条承载所选字段实际值的物理原行。"
    "没有适用列选NONE，依据不足的单位选UNPROVED。不计算金额，不推定财务准入。"
)
CONVERSION = (
    "同时定位人民币、美元折算金额列。折算币种不等于来源币种，"
    "相等或不相等的样值不能证明金额角色、缩放单位或汇率。"
)
_ROLES = {
    "amount": "引用原报交易金额列，排除余额和折算金额。",
    "balance": "引用原报余额列，没有则选NONE。",
    "currency": "引用记载来源币种的原列，没有则选NONE。不要选折算金额列。",
}
_CONVERTED = {
    "converted_cny": ("人民币", "CNY"),
    "converted_usd": ("美元", "USD"),
}
_SCALE = {table_import._UNIT["元"]: "YUAN", table_import._UNIT["分"]: "CENT",
          table_import._UNIT["角"]: "JIAO", table_import._UNIT["千元"]: "THOUSAND",
          table_import._UNIT["万元"]: "TEN_THOUSAND"}
_UNITS = {
    "YUAN": "明示以元为基础单位", "CENT": "明示以分计", "JIAO": "明示以角计",
    "THOUSAND": "明示以千元计", "TEN_THOUSAND": "明示以万元计",
    "NOT_DECLARED": "该列未明示缩放单位", "UNPROVED": "原件的缩放单位无法核定",
    "NONE": "没有适用原列",
}


def related_amount_role(name):
    """Declared neighboring amounts that do not describe the source transaction amount."""
    if table_import.is_explicit_converted_amount(name) and not table_import.explicit_converted_amount_currency(name):
        return "converted_unspecified"
    return table_import.directional_money_source_role(name)


def _could_name_role(role, name):
    amount = any(term in name for term in ("金额", "发生额", "amount"))
    conversion = any(term in name for term in ("折合", "折算", "换算", "等值", "converted", "equivalent"))
    conversion = conversion or ("折" in name and amount)
    if role in _CONVERTED:
        label = _CONVERTED[role][0]
        another_target = any(target in name for target, _ in _CONVERTED.values() if target != label)
        return label in name or conversion and not another_target
    if role == "amount":
        return amount and not conversion
    if role == "balance":
        return any(term in name for term in ("余额", "结余", "balance"))
    return any(term in name for term in ("币种", "币别", "币码", "货币", "currency"))


def _unknown_numeric_column(names, rows, classified):
    identifier_endings = ("号", "码", "标志", "标识", "类别", "类型", "日期", "时间",
                          "number", "code", "flag", "date", "time", "type", "identifier")
    for index, name in enumerate(names):
        if (index + 1 in classified or name.endswith(identifier_endings)
                or any(term in name for term in ("机构", "网点", "证件", "标志", "标识", "代码", "类别", "类型"))):
            continue
        for cells in rows.values():
            if len(cells) > index and table_import._money(cells[index]["value"], {"factor": table_import._UNIT["元"]})[0] is not None:
                return True
    return False


def _physically_blank(rows, column):
    return bool(rows) and all(len(cells) >= column and not cells[column - 1]["value"].strip()
                              for cells in rows.values())


def has_later_header(rows, profile):
    for cells in rows.values():
        values = [cell["value"] for cell in cells]
        if values and table_import._norm(values[0]) in {"续表", "接上表"}:
            return True
        candidates = table_import._candidates(values, profile)
        if len({item["index"] for matches in candidates.values() for item in matches}) > 1:
            return True
    return False


def _column_scale(role, match, rows, unit_fields, columns):
    column, factor, unit = match
    if not unit_fields:
        return "NOT_DECLARED" if unit == "元/默认基础单位" else _SCALE.get(factor, "UNPROVED")
    if any(field["index"] + 1 not in columns for field in unit_fields):
        return None
    scales = set()
    indexed = {role: [{"index": column - 1, "factor": factor, "unit": unit}], f"{role}_unit": unit_fields}
    for cells in rows.values():
        if len(cells) < column or not cells[column - 1]["value"].strip():
            continue
        checked, issues = table_import._financial_candidates([cell["value"] for cell in cells], indexed)
        resolved = checked[role][0]
        if issues or resolved["unit"] == "元/默认基础单位":
            return None
        scales.add(_SCALE.get(resolved["factor"], "UNPROVED"))
    return next(iter(scales)) if len(scales) == 1 else None


def question(purpose):
    return QUESTION + (CONVERSION if purpose == "amount_roles" else "")


def questions(data, purpose):
    columns = {value: key for key, value in data["表头"].items()}
    columns["NONE"] = "没有适用物理原列"
    rows = {position: position for position in data["原行"]}
    rows["NONE"] = "没有一行含所引字段的有效原值"
    choices = {
        **{role: (wording, columns) for role, wording in _ROLES.items()},
        "amount_unit": ("只核交易金额列自己明示的缩放单位。", _UNITS),
        "balance_unit": ("只核原报余额列自己明示的缩放单位。", _UNITS),
        "row": ("引用一条含有所引字段实际值的物理原行。", rows),
    }
    if purpose == "amount_roles":
        for role, (label, _) in _CONVERTED.items():
            choices[role] = (f"引用原件明示折算为{label}的金额列。", columns)
    return {field: {"type": "choice", "instructions": wording, "criteria": values}
            for field, (wording, values) in choices.items()}


def source_columns(headings, rows, profile, purpose):
    """Locate distinct monetary roles in the complete physical table header.

    A missing role key indicates unresolved evidence. A present key with no
    match is a verified absence from this header, independent of cell values.
    """
    names = [table_import._norm(heading) for heading in headings]
    unlabelled_values = any(any(len(cells) > index and cells[index]["value"].strip() for cells in rows.values())
                            for index, name in enumerate(names) if not name)
    indexed = table_import._candidates(headings, profile)
    possible = {role: [(item["index"] + 1, item["factor"], item["unit"])
                       for item in indexed.get(role, ())] for role in _ROLES}
    for index, heading in enumerate(headings, 1):
        base, factor, unit = table_import._header_parts(heading, profile)
        if base == "发生额" and all(column != index for column, _, _ in possible["amount"]):
            possible["amount"].append((index, factor, unit))
        if (base in table_import.SOURCE_CURRENCY_HEADINGS
                and all(column != index for column, _, _ in possible["currency"])):
            possible["currency"].append((index, factor, unit))
    if not possible["balance"]:
        for index, heading in enumerate(headings, 1):
            name, factor, unit = table_import._header_parts(heading, profile)
            if name in import_balance_trial._REPORTED_BALANCE_HEADERS:
                possible["balance"].append((index, factor, unit))
    if purpose == "amount_roles":
        for role, (_, currency) in _CONVERTED.items():
            possible[role] = [(index, table_import._UNIT["元"], None)
                              for index, name in enumerate(names, 1)
                              if table_import.explicit_converted_amount_currency(name) == currency]

    result = {}
    related = {index: role for index, name in enumerate(names, 1) if (role := related_amount_role(name))}
    conversions = {index: target for index, name in enumerate(names, 1)
                   if (target := table_import.explicit_converted_amount_currency(name)) is not None}
    separate_amounts = related.keys() | conversions.keys()
    known = separate_amounts | {column for matches in possible.values() for column, _, _ in matches}
    known.update(item["index"] + 1 for matches in indexed.values() for item in matches)
    for role, matches in possible.items():
        unit_fields = indexed.get(f"{role}_unit", ()) if role in {"amount", "balance"} else ()
        classified = {column for column, _, _ in matches} | {field["index"] + 1 for field in unit_fields}
        if role in {"amount", "currency"}:
            classified.update(separate_amounts)
        elif role in _CONVERTED:
            classified.update(index for index, label in related.items() if label != "converted_unspecified")
            classified.update(index for index, target in conversions.items() if target)
        if any(index not in classified and _could_name_role(role, name)
               and not (matches and role in {"amount", "currency"} and _physically_blank(rows, index))
               for index, name in enumerate(names, 1)):
            # A competing literal may be relevant even without a closed alias.
            continue
        if role == "balance" and not matches and _unknown_numeric_column(names, rows, known):
            continue
        if len(matches) <= 1 and (matches or not unit_fields and not unlabelled_values):
            result[role] = (matches[0] if matches else None, unit_fields)
    return result


def proof(header, rows, profile, columns, purpose, *, registered=True):
    if has_later_header(rows, profile):
        return {}
    result = {}
    for role, (match, unit_fields) in source_columns([cell["value"] for cell in header], rows, profile, purpose).items():
        if match is None:
            if not registered:
                continue
            result[role] = {"column": "NONE", "source_rows": []}
            if role in {"amount", "balance"}:
                result[role]["unit"] = "NONE"
            continue
        column, factor, _ = match
        if column not in columns:
            # An omitted candidate is different from an absent role.
            continue
        if role == "currency":
            observed = [number for number, cells in rows.items()
                        if len(cells) >= column and cells[column - 1]["value"].strip()]
        else:
            observed = import_balance_trial._numeric_rows(sorted(rows.items()), column, factor)
        if not observed:
            continue
        finding = {"column": f"C{column}", "source_rows": observed}
        if role in {"amount", "balance"}:
            scale = _column_scale(role, match, rows, unit_fields, columns)
            if scale is None:
                continue
            finding["unit"] = scale
            if unit_fields:
                finding["unit_columns"] = [field["index"] + 1 for field in unit_fields]
        if role in _CONVERTED:
            finding["conversion_currency"] = _CONVERTED[role][1]
        result[role] = finding
    return result


def _supports(answer, role, finding):
    return (answer[role] == finding["column"]
            and ("unit" not in finding or answer[f"{role}_unit"] == finding["unit"]
                 or role == "balance" and _inapplicable_balance_unit(answer, finding))
            and (finding["column"] == "NONE" or answer["row"] != "NONE"
                 and int(answer["row"][1:]) in finding["source_rows"]))


def _inapplicable_balance_unit(answer, finding):
    return (answer is not None and finding is not None and finding["column"] == "NONE"
            and finding.get("unit") == "NONE" and not finding["source_rows"]
            and answer["balance"] == "NONE" and answer["balance_unit"] == "NOT_DECLARED")


def output_interpretations(answer, findings):
    if not _inapplicable_balance_unit(answer, findings.get("balance")):
        return {}
    return {"balance_unit": {"proposal": "NOT_DECLARED", "effective": "NONE",
                             "basis": "proved_absent_balance_column"}}


def verified(answer, findings, questions):
    roles = questions.keys() - {"amount_unit", "balance_unit", "row"}
    if (answer and roles == findings.keys()
            and any(finding["source_rows"] for finding in findings.values())
            and all(_supports(answer, role, finding) for role, finding in findings.items())):
        return findings
    return None


def reviewable(drafts, findings):
    for role, finding in findings.items():
        supported = [_supports(draft, role, finding) for draft in drafts]
        if any(supported) and not all(supported):
            return True
    return False
