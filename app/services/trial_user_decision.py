"""Route a bounded, cited business question through the frozen 2+1 models."""
from dataclasses import dataclass


DEFER = "DEFER"
NO_WITNESS = "NONE"


@dataclass(frozen=True)
class Decision:
    choice: str = DEFER
    witness: str = NO_WITNESS
    proof: object = None
    reason: str = "decision_unproved"
    first_calls: int = 0
    recheck_calls: int = 0


@dataclass(frozen=True)
class Review:
    reply: object
    calls: int
    read_evidence: str | None = None
    failure: str | None = None


def review(view, judge, provider_error, *, offers=None, read=None):
    """Let the reviewer select one server-registered, read-only evidence scope."""
    offered = {**view, "read_offers": offers} if offers else view
    try:
        reply = judge(offered)
    except provider_error:
        return Review(None, 1, failure="provider_error")
    if not isinstance(reply, dict) or set(reply) != {"read"}:
        return Review(reply, 1)
    selected = reply["read"]
    if not isinstance(selected, str) or selected not in (offers or {}) or read is None:
        return Review(None, 1, failure="review_read_outside_scope")
    extra = read(selected)
    if not extra:
        return Review(None, 1, selected, "review_evidence_unavailable")
    try:
        reply = judge({**view, "added_original": {"position": selected, "evidence": extra}})
    except provider_error:
        return Review(None, 2, selected, "provider_error")
    if isinstance(reply, dict) and "read" in reply:
        return Review(None, 2, selected, "review_read_limit")
    return Review(reply, 2, selected)


def _reply(value, view):
    if not isinstance(value, dict) or set(value) != {"choice", "witness"}:
        return DEFER, NO_WITNESS
    choice, witness = value["choice"], value["witness"]
    if (not isinstance(choice, str) or choice not in view["choices"]
            or not isinstance(witness, str) or witness not in view["witnesses"]):
        return DEFER, NO_WITNESS
    return choice, witness


def decide(view, provider, tags, verify, provider_error):
    """The verifier is read-only. Only the returned claim may be adopted."""
    if provider is None:
        return Decision(reason="not_configured")
    first, claims = [], []
    calls = 0
    for tag in tags:
        calls += 1
        try:
            reply = provider.business_decision(tag, view)
        except provider_error:
            return Decision(reason="provider_error", first_calls=calls)
        choice, witness = _reply(reply, view)
        fact = verify(choice, witness) if choice != DEFER else None
        first.append({"choice": choice, "witness": witness})
        claims.append((choice, witness, fact))
    left, right = claims
    if left[0] == right[0] and left[2] is not None and right[2] is not None:
        return Decision(*left, reason="decision_agreement", first_calls=calls)
    # Disagreeing labels alone do not supply an applicable original. Recheck a
    # business conflict only when at least one claim survives product verification.
    if all(claim[2] is None for claim in claims):
        return Decision(first_calls=calls)
    reviewed = review(view, lambda material: provider.arbitrate_business(material, first), provider_error)
    if reviewed.failure:
        return Decision(reason=reviewed.failure, first_calls=calls, recheck_calls=reviewed.calls)
    choice, witness = _reply(reviewed.reply, view)
    fact = verify(choice, witness) if choice != DEFER else None
    if fact is None:
        return Decision(reason="decision_review_unproved", first_calls=calls, recheck_calls=reviewed.calls)
    return Decision(choice, witness, fact, "decision_review_accepted", calls, reviewed.calls)
