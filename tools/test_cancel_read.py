# -*- coding: utf-8 -*-
"""撤单 / 一键全撤 的「读不到 ≠ 没有委托」加固测试（离线，不碰真机）。

背景（2026-09-12 实测）：刚提交完委托时，同花顺的委托/撤单表格会有一段时间
读到**空**（真机测试里连读 6 秒都是 0 行；过几分钟再读就正常了）。
原实现把"撤单页 0 行"直接当成"没有可撤委托"：

    cancel_all_orders：0 行 -> ok=True「撤单页无可撤委托，无需操作」   ← 假成功！
    cancel_order     ：0 行 -> ok=False「非唯一，为安全不执行全撤」    ← 误导

修复：
  - _read_cancellable()：撤单页读 + 当日委托交叉验证，返回 (rows, inconclusive)
  - 一键全撤：inconclusive 时绝不报成功；点完[全撤]后复核一遍再说成功
  - 单笔撤单：inconclusive 时明确报"读取不可靠"

用法：python tools/test_cancel_read.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "executor"))

import broker_ths as m   # noqa: E402

PASS, FAIL = [], []


def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name)


# ---------------- C1: _is_alive_entrust（活单判定） ----------------

alive = m.ThsBroker._is_alive_entrust
check("C1a 未成交活单 -> True",
      alive({"委托数量": 100, "成交数量": 0, "撤消数量": 0, "备注": "未成交"}) is True)
check("C1b 已全部撤单(备注+撤消数量) -> False",
      alive({"委托数量": 100, "成交数量": 0, "撤消数量": 100, "备注": "全部撤单"}) is False)
check("C1c 备注已撤但撤消数量为0 -> False（按备注判定）",
      alive({"委托数量": 100, "成交数量": 0, "撤消数量": 0, "备注": "全部撤单"}) is False)
check("C1d 部分成交未撤完 -> True",
      alive({"委托数量": 100, "成交数量": 40, "撤消数量": 0, "备注": "部分成交"}) is True)
check("C1e 全部成交 -> False",
      alive({"委托数量": 100, "成交数量": 100, "撤消数量": 0, "备注": "成交"}) is False)
check("C1f 整行空（垃圾行）-> False 非活单",
      alive({"委托数量": "", "成交数量": "", "撤消数量": "", "备注": ""}) is False)
check("C1g 数量字段缺失 -> 保守 True",
      alive({"备注": "", "委托数量": "面议"}) is True)


# ---------------- C2-C5: cancel_all_orders ----------------

def make_broker(reads, entrust_rows=None, record=None):
    """reads：_read_with_retry 的返回值序列（末尾复用）。
    entrust_rows：query_entrusts 返回的当日委托。"""
    b = object.__new__(m.ThsBroker)
    rec = record if record is not None else {"cancel_all": 0}
    b._ensure_fg = lambda: True
    b.close_captcha_dialog = lambda: False
    st = {"i": 0}

    def fake_read(reader, name):
        i = min(st["i"], len(reads) - 1)
        st["i"] += 1
        return reads[i]

    b._read_with_retry = fake_read
    b.query_entrusts = lambda: list(entrust_rows or [])
    b.user = type("U", (), {"cancel_entrusts": []})()
    b.user.cancel_all_entrusts = lambda: rec.__setitem__("cancel_all",
                                                         rec["cancel_all"] + 1)
    b._last_captcha_ts = 0.0
    return b, rec


ALIVE_ROW = {"合同编号": "1", "委托数量": 100, "成交数量": 0, "撤消数量": 0,
             "备注": "未成交"}
DEAD_ROW = {"合同编号": "2", "委托数量": 100, "成交数量": 0, "撤消数量": 100,
            "备注": "全部撤单"}

# C2: 撤单页 0 行，但当日委托里还有活单 -> 必须报失败（绝不假成功）
b, rec = make_broker([[]], [ALIVE_ROW, DEAD_ROW])
r = b.cancel_all_orders()
check("C2 读不到但有活单 -> ok=False", r["ok"] is False)
check("C2 文案说明读取不可靠", "不可靠" in r["message"])
check("C2 未误点[全撤]", rec["cancel_all"] == 0)

# C3: 撤单页 0 行、当日委托也没有活单 -> 真·无需操作
b, rec = make_broker([[]], [DEAD_ROW])
r = b.cancel_all_orders()
check("C3 确实没有可撤 -> ok=True", r["ok"] is True)
check("C3 未误点[全撤]", rec["cancel_all"] == 0)

# C4: 正常全撤 -> 点[全撤]，复核后撤单页清空、无活单 -> 成功（带"已复核"）
b, rec = make_broker([["1 行"], []], [DEAD_ROW])
r = b.cancel_all_orders()
check("C4 正常路径 -> ok=True", r["ok"] is True)
check("C4 点了一次[全撤]", rec["cancel_all"] == 1)
check("C4 文案含已复核", "复核" in r["message"])

# C5: 点了[全撤]，但复核时还有活单 -> 不能报成功
b, rec = make_broker([["1 行"], []], [ALIVE_ROW])
r = b.cancel_all_orders()
check("C5 复核仍有活单 -> ok=False", r["ok"] is False)
check("C5 提示人工核对", "人工核对" in r["message"])


# ---------------- C6-C7: cancel_order ----------------

# C6: 读不到 + 有活单 -> 明确报"读取不可靠"（而不是误导性的"非唯一"）
b, _ = make_broker([[]], [ALIVE_ROW])
r = b.cancel_order("999")
check("C6 读不到但有活单 -> ok=False", r["ok"] is False)
check("C6 文案说明读取不可靠", "不可靠" in r["message"])

# C7: 唯一匹配 -> 走[全撤]并成功
b, rec = make_broker([[{"合同编号": "6254388722"}]], [])
r = b.cancel_order("6254388722")
check("C7 唯一匹配 -> ok=True", r["ok"] is True)
check("C7 点了一次[全撤]", rec["cancel_all"] == 1)

# C8: 多行非唯一 -> 仍拒绝（原有安全语义不变）
b, rec = make_broker([[{"合同编号": "6254388722"}, {"合同编号": "6254388723"}]], [])
r = b.cancel_order("6254388722")
check("C8 非唯一 -> 拒绝执行", r["ok"] is False and rec["cancel_all"] == 0)


# ---------------- C9: 源码级断言 ----------------

src = open(os.path.join(ROOT, "executor", "broker_ths.py"), encoding="utf-8").read()
check("C9 cancel_all_orders 使用 _read_cancellable",
      "rows, inconclusive = self._read_cancellable()" in src)
check("C9 cancel_all_orders 有复核", "复核时仍有活动委托" in src)
check("C9 cancel_order 使用 _read_cancellable",
      src.count("_read_cancellable()") >= 3)

print("\n== %d passed, %d failed ==" % (len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
