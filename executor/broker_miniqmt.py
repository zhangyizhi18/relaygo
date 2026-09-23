# -*- coding: utf-8 -*-
"""
方案C（最推荐）：miniQMT / xtquant 官方量化接口。

前置条件：
  1. 在券商开通 QMT/miniQMT 权限（部分券商有资金门槛，找客户经理可申请低门槛）
  2. 电脑上登录 miniQMT 极简客户端（必须保持登录状态，xtquant 通过它连柜台）
  3. 安装 xtquant：把 QMT 安装目录里的 xtquant 文件夹复制到执行端程序(exe)所在目录
     （与 exe 平级；源码模式则放 executor 目录），或 pip install xtquant（部分环境可用）
  4. config.py 里 QMT_PATH 填 miniQMT 的 userdata_mini 目录、QMT_ACCOUNT_ID 填资金账号

优势：官方 API，支持同步查询委托/成交/持仓，回报闭环完整，可后台运行，
     不存在 UI 自动化的"客户端升级就失效"问题。

【2026-09-15 修复】xtquant 返回值 / 字段名踩坑（用户实测 miniQMT 已登录却一直"连不上"）：
  * XtQuantTrader.connect() 返回 **int**：0 = 成功、非 0 = 失败。旧代码写
    `if not self.trader.connect()`，成功(返回 0) 时 `not 0` 恰为 True → 成功被误判为
    失败并抛异常，于是永远"连不上"（本例根因）。
  * subscribe() 同样返回 0 = 成功（不是 bool）。
  * query_stock_asset() 返回的是底层 C 结构（xtpythonclient.XtAsset，字段带 m_ 前缀），
    它 **没有 m_dBalance**；总资产是 m_dTotalAsset（旧代码取 a.m_dBalance 会 AttributeError，
    导致 health_check 永远判不健康）。
  * xtquant 对象普遍有"m_ 前缀 + 无前缀"两套字段名、且版本间有出入，故统一用 _pick 容错取值。
"""
from broker_base import BrokerBase, normalize_price, to_plain_code, to_xt_code
import market_price

try:
    from xtquant import xtconstant
    from xtquant.xttrader import XtQuantTrader
    from xtquant.xttype import StockAccount
except ImportError:
    xtconstant = None


# ---------------- 容错取值工具 ----------------

def _pick(obj, *names, default=None):
    """按候选名取第一个非 None 的值。

    xtquant 返回的对象有两套字段名（底层 C 结构带 m_ 前缀 + 无前缀别名），
    不同版本还略有差异；统一容错取值，避免一个字段改名就把整条链路打断。
    """
    for n in names:
        v = getattr(obj, n, None)
        if v is not None:
            return v
    return default


def _int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _float(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


_STATUS_NAMES = None


def _order_status_name(v):
    """委托状态码 -> 可读名（用 xtconstant 反射，避免硬编码各版本差异）。"""
    global _STATUS_NAMES
    if _STATUS_NAMES is None:
        _STATUS_NAMES = {}
        if xtconstant is not None:
            for n in dir(xtconstant):
                if n.startswith("ORDER_"):
                    try:
                        _STATUS_NAMES[int(getattr(xtconstant, n))] = n[len("ORDER_"):]
                    except Exception:
                        pass
    try:
        return _STATUS_NAMES.get(int(v), str(v))
    except Exception:
        return str(v)


def _side_name(v):
    """委托方向码 -> 中文（23 买入 / 24 卖出）。"""
    if xtconstant is not None:
        if v == getattr(xtconstant, "STOCK_BUY", 23):
            return "买入"
        if v == getattr(xtconstant, "STOCK_SELL", 24):
            return "卖出"
    return str(v)


class MiniQmtBroker(BrokerBase):
    name = "miniqmt(xtquant)"

    def __init__(self, qmt_path, account_id, account_type="STOCK"):
        self.qmt_path = qmt_path
        self.account_id = account_id
        self.account_type = account_type
        self.trader = None
        self.account = None
        self._session = 0

    # ---------------- 连接 / 关闭 ----------------

    def _close(self):
        """关掉当前连接（best-effort）。反复 connect 失败重试时不让会话/线程池堆积。"""
        t, self.trader = self.trader, None
        self.account = None
        if t is not None:
            try:
                t.stop()
            except Exception:
                pass

    def connect(self):
        if xtconstant is None:
            raise RuntimeError(
                "未找到 xtquant 库。请从 QMT 安装目录复制 xtquant 文件夹到"
                "执行端程序(exe)所在目录，或 pip install xtquant")
        self._close()
        import random
        session = random.randint(1, 999999)
        if session == self._session:
            session = (session % 999999) + 1
        self._session = session
        self.trader = XtQuantTrader(self.qmt_path, session)
        self.trader.start()
        # ★ connect() 返回 0 表示成功（int，不是 bool）
        ret = self.trader.connect()
        if ret != 0:
            self._close()
            raise RuntimeError(
                "XtQuantTrader.connect() 失败(返回码 %s)：请确认 miniQMT 极简客户端"
                "已启动并登录，且 QMT_PATH 指向 userdata_mini 目录" % ret)
        self.account = StockAccount(self.account_id, self.account_type)
        # ★ subscribe() 同样返回 0 表示成功
        sub = self.trader.subscribe(self.account)
        if sub != 0:
            self._close()
            raise RuntimeError(
                "订阅账户失败(返回码 %s)：请检查资金账号 %s、账户类型 %s 是否正确"
                % (sub, self.account_id, self.account_type))

    def health_check(self):
        """miniQMT 就绪判据：已连接且能查到资金。

        未连接时直接判未就绪（不抛异常），避免 Base 版 health_check 因 trader 为 None
        而 AttributeError（日志里只会看到一串看不懂的异常）。
        """
        if self.trader is None or self.account is None:
            return False, "%s 未连接（请检查 miniQMT 客户端是否登录）" % self.name
        try:
            self.query_balance()
            return True, "%s 连接正常" % self.name
        except Exception as e:
            return False, "%s 连接异常: %s" % (self.name, e)

    # ---------------- 下单 ----------------

    def _trade(self, direction, jq_code, price, amount):
        xt_code = to_xt_code(jq_code)              # 600519.XSHG -> 600519.SH
        price = normalize_price(jq_code, price)    # 规范价位（股票2位/ETF转债3位）
        if price is None:
            # 市价信号（price 为空/0/非法）：取最新价下单，取不到就拒绝
            price = market_price.fetch_latest(to_plain_code(jq_code))
            if price is None:
                return self._result(False, "市价信号且取不到最新价（%s），已拒绝下单" % xt_code)
            price = normalize_price(jq_code, price)
        order_type = xtconstant.STOCK_BUY if direction == "buy" else xtconstant.STOCK_SELL
        try:
            seq = self.trader.order_stock(self.account, xt_code, order_type,
                                          int(amount), xtconstant.FIX_PRICE,
                                          float(price), "jq-relay")
        except Exception as e:
            return self._result(False, "miniQMT 下单异常: %s" % e)
        # order_stock 返回委托编号：>0 成功、-1 失败（异常时可能为 None）
        if seq is None or _int(seq, -1) < 0:
            return self._result(False, "miniQMT 报单被拒（核对资金/持仓/涨跌停价、账户是否已订阅）")
        return self._result(True, "miniQMT 已报单 委托编号=%s %s %s x%s @%s"
                            % (seq, direction, xt_code, amount, price), seq)

    def buy(self, jq_code, price, amount):
        return self._trade("buy", jq_code, price, amount)

    def sell(self, jq_code, price, amount):
        return self._trade("sell", jq_code, price, amount)

    # ---------------- 查询（字段名以 2026-09-15 实测为准，中文键与控制台/dry_run 对齐） ----------------

    def query_positions(self):
        rows = self.trader.query_stock_positions(self.account) or []
        out = []
        for p in rows:
            out.append({
                "证券代码": _pick(p, "stock_code", "m_strStockCode", default=""),
                "证券名称": _pick(p, "instrument_name", "m_strInstrumentName", default=""),
                "持仓数量": _int(_pick(p, "m_nVolume", "volume")),
                "可用数量": _int(_pick(p, "m_nCanUseVolume", "can_use_volume")),
                "成本价": _float(_pick(p, "m_dOpenPrice", "open_price", "m_dAvgPrice", "avg_price")),
                "市价": _float(_pick(p, "last_price")),
                "市值": _float(_pick(p, "m_dMarketValue", "market_value")),
                "盈亏": _float(_pick(p, "float_profit")),
            })
        return out

    def query_balance(self):
        a = self.trader.query_stock_asset(self.account)
        if a is None:
            raise RuntimeError("查询资金失败（账户未就绪？）")
        # ★ 总资产是 m_dTotalAsset/total_asset，没有 m_dBalance（2026-09-15 实测）
        return {"总资产": _float(_pick(a, "m_dTotalAsset", "total_asset")),
                "可用金额": _float(_pick(a, "m_dCash", "cash")),
                "资金余额": _float(_pick(a, "m_dCurrentBalance", "current_balance")),
                "可取金额": _float(_pick(a, "m_dFetchBalance", "fetch_balance")),
                "持仓市值": _float(_pick(a, "m_dMarketValue", "market_value")),
                "冻结资金": _float(_pick(a, "m_dFrozenCash", "frozen_cash"))}

    def query_entrusts(self):
        """当日委托（Web 控制台任务用）。"""
        rows = self.trader.query_stock_orders(self.account) or []
        out = []
        for o in rows:
            oid = _int(_pick(o, "order_id", "m_nOrderID"))
            out.append({
                "委托号": oid,
                "合同编号": oid,
                "证券代码": _pick(o, "stock_code", "m_strStockCode", default=""),
                "证券名称": _pick(o, "instrument_name", "m_strInstrumentName", default=""),
                "买卖": _side_name(_pick(o, "offset_flag", "m_nOffsetFlag", "order_type", "m_nOrderType")),
                "委托价": _float(_pick(o, "m_dPrice", "price")),
                "委托量": _int(_pick(o, "m_nOrderVolume", "order_volume")),
                "已成交量": _int(_pick(o, "m_nTradedVolume", "traded_volume")),
                "状态": _order_status_name(_pick(o, "m_nOrderStatus", "order_status")),
                "时间": _pick(o, "m_nOrderTime", "order_time", default=""),
                "备注": _pick(o, "m_strStatusMsg", "status_msg", default=""),
            })
        return out

    def query_trades(self):
        """当日成交（Web 控制台任务用）。"""
        rows = self.trader.query_stock_trades(self.account) or []
        out = []
        for tr in rows:
            out.append({
                "成交编号": _pick(tr, "m_strTradedID", "traded_id", default=""),
                "证券代码": _pick(tr, "stock_code", "m_strStockCode", default=""),
                "证券名称": _pick(tr, "m_strInstrumentName", "instrument_name", default=""),
                "买卖": _side_name(_pick(tr, "offset_flag", "m_nOffsetFlag", "order_type", "m_nOrderType")),
                "成交价": _float(_pick(tr, "m_dTradedPrice", "traded_price")),
                "成交量": _int(_pick(tr, "m_nTradedVolume", "traded_volume")),
                "成交额": _float(_pick(tr, "m_dTradedAmount", "traded_amount")),
                "手续费": _float(_pick(tr, "m_dCommission", "commission")),
                "时间": _pick(tr, "m_nTradedTime", "traded_time", default=""),
                "委托号": _int(_pick(tr, "m_nOrderID", "order_id")),
            })
        return out

    # ---------------- 撤单 ----------------

    def cancel_order(self, entrust_no):
        """按委托号撤单。cancel_order_stock() 返回 0 = 成功、-1 = 失败。"""
        try:
            oid = int(str(entrust_no).strip())
        except (TypeError, ValueError):
            return self._result(False, "委托号非法: %r" % (entrust_no,))
        try:
            r = self.trader.cancel_order_stock(self.account, oid)
        except Exception as e:
            return self._result(False, "miniQMT 撤单异常: %s" % e)
        if _int(r, -1) == 0:
            return self._result(True, "已撤单 委托号=%s" % oid, oid)
        return self._result(False, "miniQMT 撤单失败(返回码 %s)，委托可能已成交或已撤销" % r)

    def cancel_all_orders(self):
        """一键全撤：取当日可撤委托逐个撤单（xtquant 无批量接口）。"""
        try:
            rows = self.trader.query_stock_orders(self.account, cancelable_only=True) or []
        except Exception as e:
            return self._result(False, "查询可撤委托失败: %s" % e)
        if not rows:
            return self._result(True, "当前无可撤委托，无需操作")
        ok = fail = 0
        for o in rows:
            oid = _int(_pick(o, "order_id", "m_nOrderID"), -1)
            if oid < 0:
                fail += 1
                continue
            try:
                if _int(self.trader.cancel_order_stock(self.account, oid), -1) == 0:
                    ok += 1
                else:
                    fail += 1
            except Exception:
                fail += 1
        if fail == 0:
            return self._result(True, "已撤销全部 %d 笔可撤委托" % ok)
        return self._result(ok > 0, "撤单：成功 %d 笔、失败 %d 笔（失败的可能已成交）" % (ok, fail))


if __name__ == "__main__":
    # 单独调试本模块：python broker_miniqmt.py
    import config as cfg
    b = MiniQmtBroker(cfg.QMT_PATH, cfg.QMT_ACCOUNT_ID, cfg.QMT_ACCOUNT_TYPE)
    b.connect()
    print("资金:", b.query_balance())
    print("持仓:", b.query_positions())
