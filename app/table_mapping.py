"""Finite identity-header grammar; no scores or value-based role guesses.

The caller supplies a normalized complete header. Existing exact aliases keep
their meaning. Only an explicit role followed by a complete identity attribute
can add a candidate; arbitrary prefixes, suffixes and annotations do not match.
"""

_ROLES = {
    "payer": ("付款方", "付款人", "付款", "汇出方", "转出方", "payer", "sender", "from"),
    "payee": ("收款方", "收款人", "收款", "汇入方", "转入方", "payee", "recipient", "to"),
    "self": ("本方", "own", "self"),
    "counter": ("对手方", "交易对手", "交易对方", "对方", "对手", "counterparty", "counter"),
}
_ATTRIBUTES = {
    "account": ("账号", "帐号", "账户", "账户号", "账户号码", "帐户号", "帐户号码",
                "account", "accountnumber", "accountno"),
    "name": ("户名", "姓名", "名称", "账户名称", "账户户名", "name", "accountname", "accountholdername"),
    "bank": ("开户银行", "开户银行名称", "开户行", "开户行名称", "银行", "银行名称", "bank", "bankname"),
}
_IDENTITIES = {}
for _role, _prefixes in _ROLES.items():
    for _attribute, _suffixes in _ATTRIBUTES.items():
        if _role == "self" and _attribute == "bank":
            continue  # No self-bank field exists in the established contract.
        for _prefix in _prefixes:
            for _suffix in _suffixes:
                _IDENTITIES.setdefault(_prefix + _suffix, set()).add(_role + "_" + _attribute)


def identity_field(header):
    candidates = _IDENTITIES.get(header, ())
    return next(iter(candidates)) if len(candidates) == 1 else None
