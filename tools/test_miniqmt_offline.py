# -*- coding: utf-8 -*-
"""
miniQMT 适配器离线回归测试 —— 不连 QMT、不连中转、不启动同花顺。

覆盖 2026-09-15 修复的一批 BUG（用户实测 miniQMT 已登录却一直"连不上"）：
  1) XtQuantTrader.connect() 返回 0 = 成功（旧代码 `if not connect()` 把成功(0)误判为失败）
  2) subscribe() 同样返回 0 = 成功
  3) query_stock_asset() 返回底层 C 结构，没有 m_dBalance（应用 m_dTotalAsset）——
     旧代码 query_balance 取 a.m_dBalance 会 AttributeError，导致 health_check 永远不健康
  4) query_positions / query_entrusts / query_trades 的字段映射（m_ 前缀 + 无前缀两套名）
  5) cancel_order / cancel_all_orders 返回值判读（0 = 成功）
  6) 模式隔离：miniqmt 模式的提示不再出现"重启同花顺"；非 ths 模式拒绝清场

跑法（项目根目录）：
    <python> tools/test_miniqmt_offline.py
"""
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXEC = os.path.join(ROOT, "executor")
os.environ["JQ_ENVFILE_NOCREATE"] = "1"      # 不让 config 自动生成 .env
os.environ["EXECUTOR_SELFCHECK"] = "1"       # 让 main 的日志落到 temp，不污染工作目录
if EXEC not in sys.path:
    sys.path.insert(0, EXEC)

_FAILED = []


def check(name, cond, extra=""):
    if cond:
        print("  [OK ] %s" % name)
    else:
        print("  [FAIL] %s %s" % (name, extra))
        _FAILED.append(name)


# ==========================================================================
# 构造 fake xtquant（必须在 import broker_miniqmt 之前注入 sys.modules）
# 刻意复刻实测到的"坑"：XtAsset 只有 m_dTotalAsset、没有 m_dBalance
# ==========================================================================

class FakeAsset(object):
    def __init__(self):
        self.m_strAccountID = "1234567890"
        self.m_dCash = 21000000.0
        self.m_dTotalAsset = 21000000.0
        self.m_dMarketValue = 0.0
        self.m_dFrozenCash = 0.0
        self.m_dFetchBalance = 21000000.0
        self.m_dCurrentBalance = 21000000.0
        # 注意：故意不定义 m_dBalance（真实 xtquant 就没有这个字段）


class FakePosition(object):
    def __init__(self):
        self.m_strStockCode = "510300.SH"
        self.instrument_name = "沪深300ETF"
        self.m_nVolume = 1000
        self.m_nCanUseVolume = 800
        self.m_dOpenPrice = 4.1
        self.m_dMarketValue = 4200.0
        self.last_price = 4.2
        self.float_profit = 100.0


class FakeOrder(object):
    def __init__(self, oid=1001, status=54):
        self.m_nOrderID = oid
        self.m_strStockCode = "510300.SH"
        self.instrument_name = "沪深300ETF"
        self.m_nOffsetFlag = 23          # 买入
        self.m_dPrice = 4.1
        self.m_nOrderVolume = 1000
        self.m_nTradedVolume = 0
        self.m_nOrderStatus = status     # 54 = ORDER_CANCELED
        self.m_nOrderTime = 93015
        self.m_strStatusMsg = "已撤"


class FakeTrade(object):
    def __init__(self):
        self.m_strTradedID = "T0001"
        self.m_strStockCode = "510300.SH"
        self.m_strInstrumentName = "沪深300ETF"
        self.m_nOffsetFlag = 23
        self.m_dTradedPrice = 4.1
        self.m_nTradedVolume = 1000
        self.m_dTradedAmount = 4100.0
        self.m_dCommission = 5.0
        self.m_nTradedTime = 93100
        self.m_nOrderID = 1001


class FakeTrader(object):
    """模拟 XtQuantTrader：connect()/subscribe() 返回 0 表示成功。"""
    connect_ret = 0
    subscribe_ret = 0
    cancel_ret = 0
    order_ret = 1001
    stopped = []

    def __init__(self, path, session, callback=None):
        self.path = path
        self.session = session

    def start(self):
        pass

    def stop(self):
        FakeTrader.stopped.append(self.session)

    def connect(self):
        return FakeTrader.connect_ret

    def subscribe(self, account):
        return FakeTrader.subscribe_ret

    def order_stock(self, *a, **k):
        return FakeTrader.order_ret

    def query_stock_asset(self, account):
        return FakeAsset()

    def query_stock_positions(self, account):
        return [FakePosition()]

    def query_stock_orders(self, account, cancelable_only=False):
        return [FakeOrder()]

    def query_stock_trades(self, account):
        return [FakeTrade()]

    def cancel_order_stock(self, account, order_id):
        return FakeTrader.cancel_ret


class FakeStockAccount(object):
    def __init__(self, account_id, account_type="STOCK"):
        self.account_id = account_id
        self.account_type = account_type


def _install_fake_xtquant():
    pkg = types.ModuleType("xtquant")
    pkg.__path__ = []
    const = types.ModuleType("xtquant.xtconstant")
    const.STOCK_BUY = 23
    const.STOCK_SELL = 24
    const.FIX_PRICE = 11
    const.ORDER_UNREPORTED = 48
    const.ORDER_REPORTED = 50
    const.ORDER_CANCELED = 54
    const.ORDER_PART_SUCC = 55
    const.ORDER_SUCCEEDED = 56
    const.ORDER_JUNK = 57
    trd = types.ModuleType("xtquant.xttrader")
    trd.XtQuantTrader = FakeTrader
    typ = types.ModuleType("xtquant.xttype")
    typ.StockAccount = FakeStockAccount
    pkg.xtconstant = const
    pkg.xttrader = trd
    pkg.xttype = typ
    sys.modules["xtquant"] = pkg
    sys.modules["xtquant.xtconstant"] = const
    sys.modules["xtquant.xttrader"] = trd
    sys.modules["xtquant.xttype"] = typ


_install_fake_xtquant()

import config                      # noqa: E402
import broker_miniqmt as M         # noqa: E402
import main as ex_main             # noqa: E402
import tasks                       # noqa: E402


def new_broker():
    return M.MiniQmtBroker(r"D:\QMT\userdata_mini", "1234567890", "STOCK")


# ==========================================================================
print("== 1. 连接：connect()/subscribe() 返回 0 = 成功 ==")
check("fake xtquant 已注入", M.xtconstant is not None)
b = new_broker()
try:
    b.connect()
    check("connect() 返回 0 时【不抛异常】（旧代码在这里会误判失败）", True)
except Exception as e:
    check("connect() 返回 0 时不应抛异常", False, "-> %s" % e)

FakeTrader.connect_ret = -1
b2 = new_broker()
try:
    b2.connect()
    check("connect() 返回 -1 时应抛异常", False, "-> 未抛")
except RuntimeError as e:
    check("connect() 返回 -1 时抛 RuntimeError", "返回码 -1" in str(e), "-> %s" % e)
FakeTrader.connect_ret = 0

FakeTrader.subscribe_ret = -1
b3 = new_broker()
try:
    b3.connect()
    check("subscribe() 返回 -1 时应抛异常", False, "-> 未抛")
except RuntimeError as e:
    check("subscribe() 返回 -1 时抛 RuntimeError", "订阅" in str(e), "-> %s" % e)
FakeTrader.subscribe_ret = 0

# ==========================================================================
print("== 2. query_balance：不再依赖不存在的 m_dBalance ==")
check("FakeAsset 确实没有 m_dBalance（复刻真实 xtquant）",
      getattr(FakeAsset(), "m_dBalance", None) is None)
b = new_broker()
b.connect()
try:
    bal = b.query_balance()
    check("query_balance() 不抛异常（旧代码取 m_dBalance 会 AttributeError）", True)
    check("总资产取到 m_dTotalAsset=21000000", bal.get("总资产") == 21000000.0, "-> %s" % bal)
    check("可用金额取到 m_dCash=21000000", bal.get("可用金额") == 21000000.0, "-> %s" % bal)
except Exception as e:
    check("query_balance() 不应抛异常", False, "-> %s" % e)

ok, msg = b.health_check()
check("health_check 已连接 -> 健康", ok is True, "-> %s/%s" % (ok, msg))
b4 = new_broker()
ok4, msg4 = b4.health_check()
check("health_check 未连接 -> 不健康且不抛异常", ok4 is False, "-> %s/%s" % (ok4, msg4))

# ==========================================================================
print("== 3. 查询字段映射 ==")
pos = b.query_positions()
check("query_positions 返回 1 条", len(pos) == 1, "-> %s" % pos)
if pos:
    p = pos[0]
    check("持仓数量=1000", p.get("持仓数量") == 1000, "-> %s" % p)
    check("可用数量=800", p.get("可用数量") == 800, "-> %s" % p)
    check("证券代码=510300.SH", p.get("证券代码") == "510300.SH", "-> %s" % p)

ent = b.query_entrusts()
check("query_entrusts 返回 1 条", len(ent) == 1, "-> %s" % ent)
if ent:
    e0 = ent[0]
    check("委托号=1001", e0.get("委托号") == 1001, "-> %s" % e0)
    check("买卖=买入（23 映射）", e0.get("买卖") == "买入", "-> %s" % e0)
    check("状态=ORDER_CANCELED（状态码 54 反射成可读名）",
          "CANCELED" in str(e0.get("状态")), "-> %s" % e0)

trs = b.query_trades()
check("query_trades 返回 1 条", len(trs) == 1, "-> %s" % trs)
if trs:
    t0 = trs[0]
    check("成交价=4.1", t0.get("成交价") == 4.1, "-> %s" % t0)
    check("手续费=5.0", t0.get("手续费") == 5.0, "-> %s" % t0)

# ==========================================================================
print("== 4. 撤单：0 = 成功 ==")
FakeTrader.cancel_ret = 0
r = b.cancel_order("1001")
check("cancel_order 返回 0 -> ok=True", r.get("ok") is True, "-> %s" % r)
FakeTrader.cancel_ret = -1
r = b.cancel_order("1001")
check("cancel_order 返回 -1 -> ok=False", r.get("ok") is False, "-> %s" % r)
FakeTrader.cancel_ret = 0
r = b.cancel_all_orders()
check("cancel_all_orders -> ok=True", r.get("ok") is True, "-> %s" % r)
r = b.cancel_order("abc")
check("cancel_order 非法委托号 -> ok=False", r.get("ok") is False, "-> %s" % r)

# ==========================================================================
print("== 5. 模式隔离（THS 与 miniqmt 不互相干扰） ==")
saved_mode = config.MODE
config.MODE = "miniqmt"
h = ex_main._recovery_hint()
check("miniqmt 模式恢复提示含 miniQMT", "miniQMT" in h, "-> %s" % h)
check("miniqmt 模式恢复提示不含'重启同花顺'", "重启同花顺" not in h, "-> %s" % h)
check("tasks 提示同样不含'重启同花顺'",
      "重启同花顺" not in tasks._mode_hint(), "-> %s" % tasks._mode_hint())
check("tasks 模式名=miniQMT", tasks._mode_label() == "miniQMT", "-> %s" % tasks._mode_label())

runner = tasks.TaskRunner(b, dry_run=False, ready_probe=lambda: (False, "未连接"))
ok_t, _res, msg_t = runner.handle({"id": 1, "kind": "query_balance", "payload": {}})
check("miniqmt 未就绪提示不含'同花顺'", "同花顺" not in msg_t, "-> %s" % msg_t)
check("miniqmt 未就绪提示含 miniQMT", "miniQMT" in msg_t, "-> %s" % msg_t)
# ths 专用任务：非 ths 模式应【先按模式拒绝】，而不是报"未就绪"（即便券商未就绪）
ok_c, _rc, msg_c = runner.handle({"id": 2, "kind": "clear_form", "payload": {}})
check("miniqmt 模式 clear_form 被拒绝", ok_c is False, "-> %s" % msg_c)
check("clear_form 拒绝理由=仅 ths 模式可用",
      "仅 ths 模式" in msg_c or "不支持清场" in msg_c, "-> %s" % msg_c)
ok_r, _rr, msg_r = runner.handle({"id": 3, "kind": "restart_ths", "payload": {}})
check("miniqmt 模式 restart_ths 被拒绝", ok_r is False, "-> %s" % msg_r)
check("restart_ths 拒绝理由=仅 ths 模式可用", "仅 ths 模式" in msg_r, "-> %s" % msg_r)

config.MODE = "ths"
h2 = ex_main._recovery_hint()
check("ths 模式恢复提示含'重启同花顺'", "重启同花顺" in h2, "-> %s" % h2)

config.MODE = "dry_run"
check("dry_run 提示不连券商", "不连接券商" in ex_main._recovery_hint(),
      "-> %s" % ex_main._recovery_hint())
config.MODE = saved_mode

# ==========================================================================
print()
if _FAILED:
    print("结果: %d 项失败 -> %s" % (len(_FAILED), _FAILED))
    sys.exit(1)
print("结果: 全部通过 ✓  (miniQMT 离线回归)")
