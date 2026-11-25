"""Allow short prose over a closed set of proved trace facts and original positions."""
from dataclasses import dataclass
from decimal import Decimal
import json
import re


_CONTEXT = ("按现有账目", "从现有账目看", "账面上")
_SINGLE_SOURCE = ("这笔入账", "这笔收入", "所选入账")
_GROUP_SOURCE = ("这些入账", "这组入账", "所选入账", "所选收入")
_SINGLE_TARGET = ("本次支出", "这笔支出", "这笔出账")
_GROUP_TARGET = ("这些支出", "这组支出", "所选支出", "所选出账")
_MODAL = ("可", "能", "可以")
_UPPER_MODAL = ("最多可", "最多能", "最多可以", "至多可", "至多能", "至多可以")
_JOIN = ("至", "到", "—", "–", "～", "-")
_BALANCE_GAP = ("本方余额还没核清", "本方余额需要再核对")
_ORDER_GAP = ("交易先后还没核清", "交易时间需要再核对")
_GAPS = {
    "source_unavailable": ("所需原件还没核齐", "所需原件仍要核对"),
    "source_clock_scope_unproved": _ORDER_GAP,
    "mixed_or_missing_time": _ORDER_GAP,
    "boundary_date_precision_unknown": _ORDER_GAP,
    "ledger_evidence_missing": _BALANCE_GAP,
    "unresolved_source_records": ("案内仍有来源记录待核", "案内还有来源记录的归属待核对"),
    "source_currency_unproved": ("有些流水的币种还没核清", "部分流水的币种还要核对"),
    "mixed_currency": ("所选流水的币种不同", "所选收支的币种不一样"),
}
_CHECK_GAPS = {
    "ledger_roles": ("本方的收支角色还没核清", "本方收支角色还要核对"),
    "currency_amount": ("币种或金额还没核清", "币种和金额需要再核对"),
    "order": _ORDER_GAP,
    "own_balance": _BALANCE_GAP,
    "interval_records": ("这段期间的流水还没核齐", "这段期间的流水还要核对"),
}
_OTHER_GAPS = ("这段账目的依据还不够", "现有材料还不够")
_MAX_TEXT = 80
_MAX_REFS = 3
_MAX_BOUND_TEXT = 300

_BOUND_ASSUMPTIONS = (
    "若所选记录有效，且原交易时钟反映资金可用顺序",
    "假设所选记录有效，且原交易时钟反映资金可用顺序",
    "若所选记录有效且资金可用顺序与原交易时钟一致",
)
_BOUND_SHARED = (
    "同一笔入账分给各目标的金额合计不能超过该笔金额",
    "同一份入账资金不能在多个目标中重复计算",
)
_BOUND_LIMITS = (
    "这些上限只按所选流水和时序计算，不保证实际可达，实际本金归属待核",
    "上限仅由所选记录及其时序约束导出，实际能否达到及本金归属仍待核",
)
_BOUND_TARGET_WORDS = ("这笔出账单独核算时至多可用", "这笔出账单独核算最多可用", "这笔出账单独核算上限为")
_BOUND_JOINT_WORDS = ("所选目标合计至多可用", "所选目标合计最多可用", "所选目标的合计上限为")
_MONEY = re.compile(r"(?:0|[1-9][0-9]{0,37})\.[0-9]{2}")


def _options(words):
    return "(?:" + "|".join(map(re.escape, words)) + ")"


def _currency_prefixes(currency):
    return ("¥", "￥", "CNY", "人民币") if currency == "CNY" else (currency,)


def _amount(currency, amount):
    value = re.escape(amount)
    prefix = _options(_currency_prefixes(currency))
    return f"(?:{prefix} *{value}|{value} *元)" if currency == "CNY" else f"{prefix} *{value}"


def _range(currency, lower, upper):
    lo, hi, join = re.escape(lower), re.escape(upper), _options(_JOIN)
    return (f"(?:{_amount(currency, lower)} *{join} *(?:{_amount(currency, upper)}|{hi})"
            f"|{lo} *{join} *{_amount(currency, upper)})(?:之间)?")


def _movement(source, target, modal, amount):
    """Allow active and passive wording over the same directed relationship."""
    credit, debit, ability = map(_options, (source, target, modal))
    return (f"(?:{credit}{ability}(?:覆盖|承担){debit}(?:中的|的)? *{amount}"
            f"|{credit}{ability}为{debit}提供 *{amount}"
            f"|{debit}{ability}由{credit}(?:覆盖|承担) *{amount})")


def _accepted_sentence(view, receipt, source, target):
    currency, lower, upper = (receipt.get(key) for key in ("currency", "lower", "upper"))
    if (not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency)
            or any(not isinstance(bound, str) or not _MONEY.fullmatch(bound) for bound in (lower, upper))):
        return None
    low, high = Decimal(lower), Decimal(upper)
    if low > high:
        return None
    view.update(币种=currency, 币种前置写法=_currency_prefixes(currency),
                币种后置写法=("元",) if currency == "CNY" else (), 可选前提=_CONTEXT,
                入账称呼=source, 支出称呼=target)
    prefix = f"(?:{_options(_CONTEXT)}[，,] *)?"
    if high == 0:
        view.update(已核证范围="零覆盖", 金额=upper, 零覆盖用词=("可覆盖金额为零", "不能提供资金", "无法提供资金"))
        credit, debit = map(_options, (source, target))
        zero = f"(?:零|{_amount(currency, upper)})"
        sentence = (f"(?:{credit}(?:在|对){debit}的可覆盖金额(?:为|是) *{zero}"
                    f"|{credit}(?:不能|无法)为{debit}提供资金"
                    f"|{debit}(?:在|由){credit}(?:那里能获得的|可覆盖的)金额(?:为|是) *{zero})")
        return prefix + sentence, "说明现有账目下没有可覆盖金额，采用零覆盖用词。"
    if low == high:
        view.update(已核证范围="同一个条件金额", 金额=upper, 可行用词=_MODAL)
        sentence = _movement(source, target, _MODAL, _amount(currency, upper))
        return prefix + sentence, "用可行措辞给出核定的账面金额。"
    if low == 0:
        view.update(已核证范围="仅上限", 上限=upper, 上限用词=_UPPER_MODAL)
        sentence = _movement(source, target, _UPPER_MODAL, _amount(currency, upper))
        return prefix + sentence, ("只给上限金额。最多或至多紧接在可、可以或能之前，金额放句末。"
                                   "词序：入账称呼、上限用词、覆盖、支出称呼、中的、金额；"
                                   "也可用入账称呼、上限用词、为、支出称呼、提供、金额。")
    view.update(已核证范围="双界", 下限=lower, 上限=upper, 可行用词=_MODAL, 连接词=_JOIN)
    sentence = _movement(source, target, _MODAL, _range(currency, lower, upper))
    return prefix + sentence, "用范围连接词按下限到上限写明两个金额。"


def _pending_words(facts, reason):
    gaps = _GAPS.get(reason)
    if gaps is None:
        gaps = next((_GAPS[code] for code in facts.get("reasons", ()) if code in _GAPS), None)
    if gaps is None:
        checks = facts.get("checks", {})
        gaps = next((words for key, words in _CHECK_GAPS.items() if checks.get(key) is False), _OTHER_GAPS)
    source_lookup = facts["kind"] == "trace_source"
    count = facts["target_count" if source_lookup else "source_count"]
    money = "这笔钱" if count == 1 else "这些钱"
    ending = "从哪来" if source_lookup else "去了哪里"
    return gaps, (f"还不能确定{money}{ending}", f"目前还查不清{money}{ending}")


@dataclass(frozen=True)
class Request:
    material: str
    schema: dict
    sentence: re.Pattern[str]
    roles: dict[str, str]
    accepted: bool
    required_refs: frozenset[str] = frozenset()

    def validated_text(self, reply):
        if not isinstance(reply, dict) or reply.keys() != {"text", "refs"}:
            return None
        text, refs = reply["text"], reply["refs"]
        fields = self.schema["properties"]
        if (not isinstance(text, str) or not 1 <= len(text) <= fields["text"]["maxLength"]
                or not self.sentence.fullmatch(text) or not isinstance(refs, list) or len(refs) > fields["refs"]["maxItems"]
                or any(not isinstance(ref, str) or ref not in self.roles for ref in refs)
                or len(set(refs)) != len(refs)):
            return None
        if self.required_refs and set(refs) != self.required_refs:
            return None
        if self.accepted and {self.roles[ref] for ref in refs} != {"source", "target"}:
            return None
        return text


def _request(view, roles, sentence, instruction, *, accepted, all_refs=False):
    required = frozenset(roles) if all_refs else frozenset()
    max_text = _MAX_BOUND_TEXT if all_refs else _MAX_TEXT
    sentence += "(?:。)?"
    # Limit decoding to the same phrase combinations the server checks again.
    refs = {"type": "string", **({"enum": list(roles)} if roles else {})}
    citations = {"type": "array", "minItems": len(required) if all_refs else 2 if accepted else 0,
                 "maxItems": len(roles) if all_refs else min(_MAX_REFS, len(roles)), "items": refs}
    if all_refs:
        # The selected postings are all necessary. A per-item enum permits
        # duplicate aliases and can silently omit a whole side of the ledger.
        citations["enum"] = [list(roles)]
    schema = {"type": "object", "additionalProperties": False, "required": ["text", "refs"], "properties": {
        "text": {"type": "string", "minLength": 1, "maxLength": max_text,
                 "pattern": "^" + sentence.replace("(?:", "(") + "$"},
        "refs": citations}}
    material = ("用中文写追踪说明，仅依下列产品核证事实及用词。只输出 JSON 的 text 和 refs。"
                + instruction + "\n" + json.dumps(view, ensure_ascii=False, separators=(",", ":")))
    return Request(material, schema, re.compile(sentence), roles, accepted, required)


def _selected_order_request(packet, positions):
    facts = packet["facts"]
    bound = facts["selected_order_upper_bound"]
    currency, joint, targets = bound["currency"], bound["joint_upper"], bound["targets"]
    if not re.fullmatch(r"[A-Z]{3}", currency) or not _MONEY.fullmatch(joint):
        return None
    sources = facts["source_refs"]
    target_refs = [row["ref"] for row in targets]
    rows = {record["id"]: record["locator"].get("source_row") for record in packet["records"]}
    selected = set(sources) | set(target_refs)
    # Only generate when every selected record can be opened at its original.
    if (not sources or not targets or len(sources) != facts["source_count"]
            or len(targets) != facts["target_count"] or set(target_refs) != set(facts["target_refs"])
            or len(selected) != len(sources) + len(targets) or not selected <= positions
            or any(type(row["source_row"]) is not int or rows.get(row["ref"]) != row["source_row"]
                   or not _MONEY.fullmatch(row["upper"]) for row in targets)):
        return None
    aliases = {ref: f"R{index}" for index, ref in enumerate([*sources, *target_refs], 1)}
    roles = {alias: "source" if ref in sources else "target" for ref, alias in aliases.items()}
    view = {
        "依据": "FundsTrace 核对的所选原行和条件上界",
        "前提用词": _BOUND_ASSUMPTIONS,
        "目标": [{"引文": aliases[row["ref"]], "原行": row["source_row"], "单独上界": row["upper"]} for row in targets],
        "单独上界用词": _BOUND_TARGET_WORDS, "合计上界用词": _BOUND_JOINT_WORDS,
        "币种": currency, "币种前置写法": _currency_prefixes(currency), "合计上界": joint,
        "容量说明用词": _BOUND_SHARED, "结论限制用词": _BOUND_LIMITS, "引文编号及角色": roles,
    }
    target_terms = [f"原行 *{row['source_row']} *{_options(_BOUND_TARGET_WORDS)} *{_amount(currency, row['upper'])}"
                    for row in targets]
    parts = [_options(_BOUND_ASSUMPTIONS), *target_terms,
             _options(_BOUND_JOINT_WORDS) + " *" + _amount(currency, joint),
             _options(_BOUND_SHARED), _options(_BOUND_LIMITS)]
    instruction = ("按顺序写前提、每个目标单独核算的上界、合计上界、容量说明及结论限制。"
                   "每个目标以原行和行号开头，金额须有币种。各部分用中文逗号或句号隔开。"
                   "refs 按给出的顺序各填写一次所有引文编号，编号只出现在 refs。")
    return _request(view, roles, "[，。] *".join(parts), instruction, accepted=False, all_refs=True)


def prepare(packet, receipt, positions):
    """Describe selected roles and verified citations without sending row values."""
    facts = packet["facts"]
    kind = facts.get("kind")
    if kind not in {"trace_source", "trace_target"} or receipt is None or receipt["proposition"] != "conditional_range":
        return None
    accepted = receipt["status"] == "accepted"
    if accepted:
        positions = positions.intersection(receipt["refs"])
    elif (facts.get("selected_source_flow_ids") and "selected_order_upper_bound" in facts
          and receipt["reason"] != "source_unavailable"):
        return _selected_order_request(packet, positions)
    groups = {side: [ref for ref in facts[side + "_refs"] if ref in positions]
              for side in ("source", "target")}
    roles = {}
    for side, refs in groups.items():
        for _ in refs:
            roles[f"R{len(roles) + 1}"] = side
    view = {"方向": "核对来源" if kind == "trace_source" else "核对流向", "引文编号及角色": roles}
    if accepted:
        if not all(groups.values()):
            return None
        source = _SINGLE_SOURCE if facts["source_count"] == 1 else _GROUP_SOURCE
        target = _SINGLE_TARGET if facts["target_count"] == 1 else _GROUP_TARGET
        supported = _accepted_sentence(view, receipt, source, target)
        if supported is None:
            return None
        sentence, instruction = supported
        instruction += "组织成简短自然的一句，可用‘覆盖…中的’、‘为…提供’或‘由…覆盖’。引文至少各选一个 source 和 target。"
    else:
        gaps, endings = _pending_words(facts, receipt["reason"])
        view.update(状态="未决", 缺口用词=gaps, 结论用词=endings)
        instruction = ("先用缺口用词指出还要核对什么，再接逗号和结论。"
                       "未决句无金额或数字。引文从给出的编号选，无则空数组。")
        sentence = f"{_options(gaps)}[，,] *{_options(endings)}"
    return _request(view, roles, sentence, instruction, accepted=accepted)
