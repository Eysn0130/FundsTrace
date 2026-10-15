# -*- coding: utf-8 -*-
"""生成演示用流水数据：含多层快进快出的电诈资金链条 + 日常噪音交易"""
import datetime
import random

from .importer import commit_rows

SURNAMES = "张王李赵刘陈杨黄周吴徐孙马朱胡郭何高林郑谢罗梁宋唐许韩冯邓曹彭"
GIVEN = ["伟", "芳", "娜", "敏", "静", "丽", "强", "磊", "军", "洋", "勇", "艳", "杰", "娟", "涛", "明",
         "超", "霞", "平", "刚", "辉", "宇", "欣", "怡", "子涵", "雨轩", "浩然", "梦琪", "思远", "晓东"]
BANKS = {"工商银行": "6222", "建设银行": "6217", "农业银行": "6228", "中国银行": "6216",
         "邮储银行": "6210", "招商银行": "6225", "平安银行": "6230", "农信社": "6229"}


def generate(case_id=1):
    rng = random.Random(20260925)
    base = datetime.datetime(2026, 3, 1, 8, 0, 0)

    def mk(role):
        bank = rng.choice(list(BANKS))
        return {"name": rng.choice(SURNAMES) + rng.choice(GIVEN),
                "acct": BANKS[bank] + "".join(rng.choice("0123456789") for _ in range(rng.randint(11, 14))),
                "bank": bank, "role": role}

    victims = [mk("victim") for _ in range(10)]
    l1 = [mk("mule") for _ in range(6)]      # 一级卡（直接收款）
    l2 = [mk("mule") for _ in range(12)]     # 二级卡（拆分转移）
    l3 = [mk("mule") for _ in range(8)]      # 三级卡（归集）
    cash = [mk("cash") for _ in range(3)]    # 取现/沉淀卡
    normals = [mk("normal") for _ in range(22)]

    rows = []
    seq = [0]

    def add(p, q, t, amt, summary):
        seq[0] += 1
        rows.append({
            "trade_no": f"D{seq[0]:07d}",
            "trade_time": t,
            "payer_name": p["name"], "payer_account": p["acct"], "payer_bank": p["bank"],
            "payee_name": q["name"], "payee_account": q["acct"], "payee_bank": q["bank"],
            "amount": round(amt, 2), "balance": None,
            "summary": summary,
            "channel": rng.choice(["网银", "手机银行", "ATM", "柜面", "第三方支付"]),
        })

    # 日常噪音交易
    for _ in range(1500):
        a, b = rng.sample(normals + victims, 2)
        t = base + datetime.timedelta(days=rng.randint(0, 208), seconds=rng.randint(0, 86399))
        add(a, b, t, rng.uniform(50, 9000),
            rng.choice(["消费", "还款", "代发工资", "网购退款", "日常转账", "红包", "水电费"]))

    # 电诈链条：受害者 → 一级卡 → 二级卡(拆分) → 三级卡(归集) → 取现卡
    for ev in range(60):
        t0 = base + datetime.timedelta(days=int(ev * 3.4), seconds=rng.randint(8 * 3600, 22 * 3600))
        v, l = rng.choice(victims), rng.choice(l1)
        amt = round(rng.uniform(5000, 120000), 2)
        add(v, l, t0, amt, rng.choice(["借款", "货款", "投资返利", "刷单垫付", "保证金", "解冻费"]))
        remain = amt * rng.uniform(0.88, 0.97)
        kids = rng.sample(l2, rng.randint(2, 3))
        weights = [rng.uniform(0.6, 1.4) for _ in kids]
        wsum = sum(weights)
        t = t0
        for k, w in zip(kids, weights):
            t = t + datetime.timedelta(minutes=rng.uniform(1, 12), seconds=rng.randint(0, 59))
            sub = round(remain * w / wsum, 2)
            if sub < 100:
                continue
            add(l, k, t, sub, rng.choice(["转账", "还款", "货款", "退款"]))
            if rng.random() < 0.85:
                t2 = t + datetime.timedelta(minutes=rng.uniform(2, 25))
                k2 = rng.choice(l3)
                add(k, k2, t2, sub * rng.uniform(0.80, 0.97), rng.choice(["转账", "还款", "消费退款"]))

    # 三级卡定期归集到取现卡
    for c in cash:
        for _ in range(7):
            src = rng.choice(l3)
            t = base + datetime.timedelta(days=rng.randint(10, 205), seconds=rng.randint(9 * 3600, 20 * 3600))
            add(src, c, t, rng.uniform(20000, 180000), rng.choice(["转账", "理财赎回", "消费退款"]))

    rows.sort(key=lambda r: r["trade_time"])
    return commit_rows("演示数据（自动生成）", "demo", rows,
                       {"total": len(rows), "ok": len(rows), "skipped": 0}, case_id=case_id)
