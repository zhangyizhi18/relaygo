# -*- coding: utf-8 -*-
"""
聚宽模拟器 全流程边界测试（无头，不起 GUI）。
覆盖：四函数语义 / 限价挂单冻结与释放 / 撤单 / 撮合 / T+1 / 整手 /
      异常输入（限价<=0、负价标的、空代码、超额挂单）/ payload 契约。
运行: python joinquant/jq_sim_gui.py --fulltest
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jq_sim_gui import SimEngine  # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("OK   %s" % name)
    else:
        FAIL += 1
        print("FAIL %s  %s" % (name, detail))


def main():
    # ---------- 基础四函数 ----------
    e = SimEngine(cash=1000000)
    e.add_security("600519.XSHG", "贵州茅台", 100.0)
    e.add_security("510300.XSHG", "沪深300ETF", 3.923)

    o = e.order("600519.XSHG", 200)
    check("市价买入立即成交", o and o["status"] == "held")
    check("成交扣款正确", abs(e.cash - (1000000 - 20000)) < 1e-6, e.cash)
    check("T+1 当日买不可卖", e.closeable("600519.XSHG") == 0)
    check("T+1 卖出拒绝返回 None", e.order("600519.XSHG", -100) is None)
    e.t1 = False
    o = e.order("600519.XSHG", -100)
    check("关闭T+1后可卖", o and o["side"] == "sell")

    check("非整手买入拒绝", e.order("600519.XSHG", 150) is None)
    check("资金不足拒绝", e.order("600519.XSHG", 10 ** 9) is None)
    check("空代码拒绝", e.order("", 100) is None)
    check("未知代码拒绝", e.order("999999.XSHG", 100) is None)
    check("0数量拒绝", e.order("600519.XSHG", 0) is None)

    # ---------- target 系列 ----------
    o = e.order_target("600519.XSHG", 400)
    check("order_target 差额下单", o and o["amount"] == 300 and o["side"] == "buy",
          "当前持仓100,目标400,差额应为300: %s" % (o and o["amount"]))
    check("order_target 已达标 None", e.order_target("600519.XSHG", 400) is None)
    o = e.order_value("510300.XSHG", 3923)          # 3.923 -> 1000 股整手
    check("order_value 整手取整", o and o["amount"] == 1000, o)
    o = e.order_target_value("600519.XSHG", 0)
    check("order_target_value 清仓", o and "600519.XSHG" not in e.positions)
    check("order_target_value 已达标 None", e.order_target_value("600519.XSHG", 0) is None)
    check("order_value 卖出金额过小 None", e.order_value("510300.XSHG", -1) is None)

    # ---------- 限价挂单 + 冻结 ----------
    e2 = SimEngine(cash=100000)
    e2.add_security("600519.XSHG", "贵州茅台", 100.0)
    b1 = e2.order("600519.XSHG", 300, limit_price=100.0)     # 挂单买 300 股, 冻结 30000
    check("限价单挂 open", b1 and b1["status"] == "open")
    check("挂单冻结资金", abs(e2.available_cash() - 70000) < 1e-6, e2.available_cash())
    check("冻结资金再下单被拒(超买)", e2.order("600519.XSHG", 800, limit_price=100.0) is None)
    check("市价单也受冻结约束", e2.order("600519.XSHG", 800) is None)
    ok = e2.cancel_order(b1["order_id"])
    check("撤单成功并解冻", ok and abs(e2.available_cash() - 100000) < 1e-6,
          e2.available_cash())

    # 卖出挂单冻结：防重复挂单超卖
    e2.reset(100000)
    e2.t1 = False
    o = e2.order("600519.XSHG", 500)                          # 市价买 500
    check("建仓500股", o and e2.positions["600519.XSHG"]["total_amount"] == 500)
    s1 = e2.order("600519.XSHG", -300, limit_price=105.0)     # 挂卖 300
    check("挂卖单 open", s1 and s1["status"] == "open")
    check("挂卖冻结可卖(剩200)", e2.closeable("600519.XSHG") == 200)
    check("重复挂卖超卖被拒", e2.order("600519.XSHG", -300, limit_price=105.0) is None)
    check("市价卖出也受冻结约束", e2.order("600519.XSHG", -300) is None)
    ok, msg = e2.match_order(s1["order_id"])
    check("挂卖撮合成交", ok and e2.positions["600519.XSHG"]["total_amount"] == 200, msg)
    check("撮合后冻结清零", e2.closeable("600519.XSHG") == 200)

    # 限价 0 / 负价
    check("限价0拒绝", e2.order("600519.XSHG", 100, limit_price=0) is None)
    check("限价负数拒绝", e2.order("600519.XSHG", 100, limit_price=-5) is None)

    # ---------- 行情 / 快照 ----------
    check("添加负价标的拒绝", e2.add_security("000001.XSHE", "平安银行", -5) is False)
    check("添加0价标的拒绝", e2.add_security("000001.XSHE", "平安银行", 0) is False)
    check("改价负数拒绝", e2.set_price("600519.XSHG", -1) is False)
    check("改价正常", e2.set_price("600519.XSHG", 101.0) is True)
    snap = e2.positions_snapshot()
    check("快照结构正确", snap and all(set(x) >= {"security", "total_amount",
                                                  "closeable_amount"} for x in snap), snap)

    # ---------- payload 契约 ----------
    e3 = SimEngine()
    e3.add_security("600519.XSHG", "贵州茅台", 100.0)
    p1 = e3.order("600519.XSHG", 100, limit_price=99.0)
    pay = SimEngine.build_payload(p1)
    check("payload 六字段完全一致",
          pay == {"order_id": str(p1["order_id"]), "security": "600519.XSHG",
                  "side": "buy", "amount": 100, "price": 99.0, "jq_status": "open"}, pay)
    p2 = e3.order("600519.XSHG", 100)
    pay2 = SimEngine.build_payload(p2)
    check("市价 payload price 为最新价非空", pay2["price"] == 100.0 and pay2["jq_status"] == "held")

    print("\n===== 引擎边界测试: %d 过 / %d 挂 =====" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
