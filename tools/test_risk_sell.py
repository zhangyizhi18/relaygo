# -*- coding: utf-8 -*-
"""风控模块离线回归：卖出必须看得到持仓（2026-09-13 实测 bug 的回归）。

背景（真机实测发现）：
    用模拟账号发**卖出信号**（588170 持仓 15300 股、卖 100 股），执行端回报
        「风控拒绝 #79 588170.XSHG sell held x100 @0.903: 卖出 100 股但实际持仓仅 0 股」
    这笔单**根本没到券商** —— 而控制台查持仓明明是 15300 股。
    根因：risk.py 用 `p.get("amount", 0)` 读持仓，而本项目的
    broker_ths.query_positions() 直接返回 easytrader 的**中文列名**行
    （证券代码 / 股票余额 / 可用余额 …），没有 `amount` 键 → held 恒为 0
    → **所有卖出信号都被自家风控拦掉**。

本测试用 fake broker 覆盖多套字段名，确保：
    * 中文列名（可用余额）能读到；
    * 英文列名（amount）能读到；
    * 没有持仓 -> 拒绝（不能放行裸卖空）；
    * 可卖不足（T+1：股票余额够但可用余额不够）-> 拒绝；
    * 查询持仓抛异常 -> 拒绝（宁拒不错）。

跑法：venv\\Scripts\\python.exe tools\\test_risk_sell.py
"""
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "executor"))

import risk            # noqa: E402

FAIL = []
N = [0]


def check(name, cond, detail=""):
    N[0] += 1
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  <- " + str(detail)) if (detail and not cond) else ""))
    if not cond:
        FAIL.append(name)


def hr(t):
    print("\n" + "=" * 74)
    print(t)
    print("=" * 74)


class FakeBroker(object):
    def __init__(self, rows=None, boom=None):
        self._rows = rows
        self._boom = boom
        self.calls = 0

    def query_positions(self):
        self.calls += 1
        if self._boom:
            raise RuntimeError(self._boom)
        return self._rows


def sig(code="588170.XSHG", side="sell", amount=100, price=None):
    s = {"security": code, "side": side, "amount": amount, "jq_status": "held"}
    if price is not None:
        s["price"] = price
    return s


# 关掉与本次无关的闸（白名单/额度），只测"卖出持仓校验"这一段
risk.config.CODE_WHITELIST = set()
risk.config.MAX_SINGLE_AMOUNT = 20000      # 放大单笔上限，免得它先于"持仓校验"拦下来
risk.config.MAX_DAILY_AMOUNT = 100000


hr("[1] 本机 broker_ths 的真实列名：中文「可用余额」")
rows = [{"证券代码": "588170", "证券名称": "科创半导", "股票余额": 15300,
         "可用余额": 15300, "市价": 0.895, "成本价": 1.03}]
ok, why = risk.check(sig(amount=100), broker=FakeBroker(rows))
check("有 15300 股可卖、卖 100 股 -> 放行（修复前必被拒）", ok, why)
ok, why = risk.check(sig(amount=15300), broker=FakeBroker(rows))
check("卖满 15300 股 -> 放行", ok, why)
ok, why = risk.check(sig(amount=15400), broker=FakeBroker(rows))
check("卖 15400 股（超过可卖）-> 拒绝", not ok, why)

hr("[2] T+1：可用余额 < 股票余额（当天买入不可卖）")
rows2 = [{"证券代码": "510300", "股票余额": 1000, "可用余额": 0}]
ok, why = risk.check(sig("510300.XSHG", amount=100), broker=FakeBroker(rows2))
check("股票余额 1000 / 可用余额 0，卖 100 -> 拒绝（当天买入不可卖）", not ok, why)
rows3 = [{"证券代码": "510300", "股票余额": 1000, "可用余额": 500}]
ok, why = risk.check(sig("510300.XSHG", amount=400), broker=FakeBroker(rows3))
check("可用 500，卖 400 -> 放行", ok, why)
ok, why = risk.check(sig("510300.XSHG", amount=600), broker=FakeBroker(rows3))
check("可用 500，卖 600 -> 拒绝", not ok, why)

hr("[3] 兼容英文列名（其它适配器 / 旧格式）")
rows4 = [{"security": "588170.XSHG", "amount": 300}]
ok, why = risk.check(sig(amount=200), broker=FakeBroker(rows4))
check("amount=300 卖 200 -> 放行", ok, why)
ok, why = risk.check(sig(amount=400), broker=FakeBroker(rows4))
check("amount=300 卖 400 -> 拒绝", not ok, why)

hr("[4] 边界：空持仓 / 其它代码 / 查询异常")
ok, why = risk.check(sig(), broker=FakeBroker([]))
check("持仓为空 -> 拒绝", not ok, why)
ok, why = risk.check(sig(), broker=FakeBroker([{"证券代码": "510300", "可用余额": 5000}]))
check("持仓里只有别的代码 -> 拒绝（不做裸卖空）", not ok, why)
ok, why = risk.check(sig(), broker=FakeBroker(boom="剪贴板读取异常"))
check("查持仓抛异常 -> 拒绝且原因是查询失败", (not ok) and "查询持仓失败" in why, why)

hr("[5] 买入不受持仓校验影响")
b = FakeBroker([])
ok, why = risk.check(sig("510300.XSHG", side="buy", amount=100), broker=b)
check("买入 100 股（无持仓）-> 放行（资金交给券商判）", ok, why)
check("...且买入路径没有去查持仓", b.calls == 0, "query_positions 被调用 %d 次" % b.calls)

hr("汇总")
print("  %d 项通过 / %d 项失败" % (N[0] - len(FAIL), len(FAIL)))
if FAIL:
    print("  失败项:", FAIL)
sys.exit(1 if FAIL else 0)
