"""Bounded pairing policy for effective-funds analysis, not bank settlement.

Only small matching facts are retained while parsing. The source ZIP remains
the sole raw record store, and this module has no database or filesystem access.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, localcontext
import re


POLICY = "paired-cancellation-v1"
PAIR_FIELDS = frozenset({"cancellation_policy", "cancellation_role", "paired_record_no",
                         "cancellation_witness_record_no"})
_ACTION = re.compile(r"冲正|撤销")
_FRAGMENTS = re.compile(r"[,，;；。\r\n]+")
# Action-bearing fields have a closed grammar. Other fields supply no action
# evidence; only these complete result labels veto an otherwise matched action.
# Ordinary business descriptions are not interpreted through substring rules.
_NEUTRAL_ANNOTATIONS = frozenset({"无", "无异常", "交易正常", "未发现异常"})
_NON_AFFIRMATIVE_RESULTS = frozenset({"失败", "未完成", "无法办理", "处理中", "未知状态", "状态未知", "待确认"})
_LABEL = r"(?:冲正撤销|交易冲正|交易撤销|冲正交易|撤销交易|消费冲正|转账冲正|冲正|撤销)"
_PREFIX = r"(?:已(?:经)?(?:成功|完成)?|成功|完成)"
_SUFFIX = r"(?:已(?:经)?)?(?:成功|完成)"
_ANNOTATION = re.compile(
    r"(?:" + _PREFIX + r"\s*)?" + _LABEL
    + r"(?:\s*(?:" + _SUFFIX + r"|（(?:成功|完成)）|\((?:成功|完成)\)))?"
)


def cancellation_text(source):
    found = False
    for column in ("摘要说明", "备注", "交易类型"):
        fragments = [fragment.strip() for fragment in _FRAGMENTS.split(source[column]) if fragment.strip()]
        if not _ACTION.search(source[column]):
            if any(fragment in _NON_AFFIRMATIVE_RESULTS for fragment in fragments):
                return False
            continue
        for fragment in fragments:
            if fragment in _NEUTRAL_ANNOTATIONS:
                continue
            if _ANNOTATION.fullmatch(fragment):
                found = True
            else:
                return False
    return found


@dataclass(slots=True)
class Observation:
    number: int
    scope: tuple | None
    reference: str
    time: datetime | None
    amount: Decimal | None
    balance: Decimal | None
    direction: str | None
    accepted: bool
    eligible: bool
    feedback_only: bool


class CancellationIndex:
    """Find unique two-leg round trips with O(n log n) time indexes."""

    def __init__(self):
        self.rows = []
        self.times = defaultdict(lambda: defaultdict(list))
        self.references = defaultdict(list)
        self.unlocated = set()

    def add(self, observation):
        self.rows.append(observation)
        if observation.scope is None:
            return
        if observation.time is not None:
            self.times[observation.scope][observation.time].append(observation)
        elif observation.amount != 0 and not observation.feedback_only:
            self.unlocated.add(observation.scope)
        if observation.reference:
            self.references[observation.scope, observation.reference].append(observation)

    def pairs(self):
        prior = {}
        for scope, times in self.times.items():
            previous = None
            for time in sorted(times):
                current = times[time]
                if len(current) == 1 and previous is not None and len(previous) == 1:
                    prior[current[0].number] = previous[0]
                previous = current
        pairs = []
        with localcontext() as context:
            context.prec = 40
            for cancellation in self.rows:
                if (not cancellation.eligible or cancellation.scope in self.unlocated
                        or not cancellation.reference or cancellation.balance is None):
                    continue
                reference = self.references[cancellation.scope, cancellation.reference]
                if len(reference) != 2:
                    continue
                original = reference[0] if reference[1] is cancellation else reference[1]
                if (not original.accepted or original.amount is None or original.amount <= 0
                        or original.amount != cancellation.amount.copy_negate()
                        or original.direction is None or original.direction != cancellation.direction
                        or prior.get(cancellation.number) is not original or original.balance is None):
                    continue
                witness = prior.get(original.number)
                if witness is None or witness.balance is None:
                    continue
                effect = original.amount if original.direction == "in" else original.amount.copy_negate()
                if (original.balance - witness.balance == effect
                        and cancellation.balance - original.balance == effect.copy_negate()
                        and cancellation.balance == witness.balance):
                    pairs.append({"original_record_no": original.number,
                                  "cancellation_record_no": cancellation.number,
                                  "witness_record_no": witness.number})
        return pairs
