"""Finite identity-header grammar; no scores or value-based role guesses.

The caller supplies a normalized complete header. Existing exact aliases keep
their meaning. Only an explicit role followed by a complete identity attribute
can add a candidate; arbitrary prefixes, suffixes and annotations do not match.
"""

import re

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


ACCOUNT_CARD_FIELDS = frozenset(("self_account", "self_card", "payer_account", "payee_account",
                                 "counter_account", "counter_card"))
_DOCUMENT_ATTRIBUTES = ("证件号", "证件号码", "证件编号", "身份证", "身份证号", "身份证号码",
                        "身份证件号", "身份证件号码", "居民身份证号", "居民身份证号码", "证照号", "证照号码",
                        "idcardnumber", "identitycardnumber", "identitydocumentnumber")
_DOCUMENT_OWNERS = ("", "交易", "交易方", "开户人", "持卡人", "客户", "本端", "对端", "代办人", "法人代表", "备案",
                    *(prefix for prefixes in _ROLES.values() for prefix in prefixes))
_DOCUMENT_NUMBERS = frozenset(owner + attribute for owner in _DOCUMENT_OWNERS for attribute in _DOCUMENT_ATTRIBUTES)


def document_number_header(normalized_header):
    # Unique labels can include the physical column suffix, or the historical
    # reader's duplicate-name suffix. Neither changes the header's role.
    header = re.sub(r"(?:\[列[1-9][0-9]*\]|\.[1-9][0-9]*)$", "", normalized_header)
    return header.removesuffix("(查询条件)") in _DOCUMENT_NUMBERS
