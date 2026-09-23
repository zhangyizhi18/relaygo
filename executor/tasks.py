# -*- coding: utf-8 -*-
"""
控制台任务执行器 —— 把 Web 控制台下发的任务映射到券商适配器。

和聚宽信号(cignals)的区别：
  * 信号是"策略批量、异步"的，执行端签收后回报 accepted 即可；
  * 任务是"人在控制台上手动点按钮"发起的，必须把查询数据、委托号等原样回报，
    控制台在另一端同步等着这条结果。

支持的任务类型（与 relay_server/store_web.py 的 TASK_KINDS 一一对应）：
  query_balance   查资金
  query_positions 查持仓
  query_entrusts  查当日委托
  query_trades    查当日成交
  place_order     下单（同样过 risk.check 风控，绝不因为"是人工下的"就跳过）
  cancel_order    撤单
  cancel_all_orders 一键全撤（撤销当日全部可撤委托）

dry_run 模式（broker 为 None）：返回模拟数据并明确标注，方便在没有券商的环境跑通控制台。
"""
import logging
import traceback

import config
import risk
from broker_base import to_plain_code

log = logging.getLogger("executor.tasks")


def _mode_label():
    """当前券商的显示名（避免在 miniqmt 模式里把券商称作"同花顺"）。"""
    return {"miniqmt": "miniQMT", "thsauto": "thsauto",
            "ths": "同花顺"}.get(getattr(config, "MODE", ""), "券商")


def _mode_hint():
    """券商未就绪时的下一步指引 —— 按模式区分，避免 ths 与 miniqmt 互相干扰。"""
    mode = getattr(config, "MODE", "")
    if mode == "miniqmt":
        return "请确认 miniQMT 极简客户端已启动并登录"
    if mode == "thsauto":
        return "请确认雷电模拟器与 thsauto 服务已启动"
    if mode == "dry_run":
        return "dry_run 模式不连接券商"
    return "可在本页点「重启同花顺」恢复后再试"


def fetch_last_price(jq_code):
    """取最新价（腾讯免费行情接口，不需要 key）。失败返回 None。"""
    import requests
    code = to_plain_code(jq_code)
    market = "sh" if jq_code.endswith(".XSHG") else "sz"
    try:
        r = requests.get("http://qt.gtimg.cn/q=%s%s" % (market, code), timeout=5,
                         proxies={"http": None, "https": None})
        r.encoding = "gbk"
        parts = r.text.split("~")
        if len(parts) > 3 and parts[3]:
            return float(parts[3])
    except Exception as e:
        log.warning("获取最新价失败 %s: %s", jq_code, e)
    return None


class TaskRunner(object):
    # 需要"券商已就绪"才能真正执行的任务；「重启同花顺」「解除熔断」属恢复手段，
    # 恰恰要在券商没连上时可用，因此**不在**此列（否则同花顺一出问题就无路可走）。
    READY_REQUIRED = ("query_balance", "query_positions", "query_entrusts",
                      "query_trades", "place_order", "cancel_order",
                      "cancel_all_orders", "clear_form")
    # 仅 ths 模式支持的任务（同花顺 UI 专用）：非 ths 模式直接拒绝，
    # 不进入"未就绪等待"，否则 miniqmt 模式下点「清场/重启同花顺」会先被
    # "miniQMT 尚未就绪"拦下（驴唇不对马嘴）——2026-09-15 修。
    THS_ONLY = {"clear_form": "清场", "restart_ths": "重启同花顺"}

    def __init__(self, broker, guard=None, dry_run=None, ready_probe=None):
        self.broker = broker      # dry_run 模式下为 None
        self.guard = guard        # 连续失败熔断守卫（None 表示执行端未启用）
        # dry_run：明确传入（broker 为 None 时默认 True，兼容旧调用方）
        self.dry_run = (broker is None) if dry_run is None else bool(dry_run)
        # ready_probe: 可调用对象 -> (bool, 原因)；用于在券商未就绪时给出明确指引，
        # 而不是让任务去操作一个 None 连接、抛一堆看不懂的异常。None = 不做前置检查。
        self.ready_probe = ready_probe

    # ---------------- 唯一入口 ----------------

    def handle(self, task):
        """执行一条任务。返回 (ok, result, message)。任何异常都转成失败回报。"""
        kind = task.get("kind")
        payload = task.get("payload") or {}
        tid = task.get("id")
        log.info("收到控制台任务 #%s [%s] %s", tid, kind, payload)
        fn = getattr(self, "_do_" + str(kind), None)
        if fn is None:
            return False, None, "执行端不支持的任务类型: %s" % kind
        # 仅 ths 模式支持的任务：非 ths 模式直接拒绝（先于"未就绪"检查）
        if (not self.dry_run and kind in self.THS_ONLY
                and getattr(config, "MODE", "") != "ths"):
            return False, None, "当前模式(%s)不支持%s（该操作仅 ths 模式可用）" % (
                getattr(config, "MODE", ""), self.THS_ONLY[kind])
        # 券商未就绪的前置检查：给用户一句能照做的指引（而不是"任务执行异常"）
        if (not self.dry_run and kind in self.READY_REQUIRED
                and self.ready_probe is not None):
            try:
                ready, reason = self.ready_probe()
            except Exception:
                ready, reason = True, ""
            if not ready:
                return False, None, "%s尚未就绪（%s）；%s" % (
                    _mode_label(), reason or "未连接", _mode_hint())
        try:
            ok, result, message = fn(payload)
            log.info("任务 #%s 完成 ok=%s %s", tid, ok, message)
            return ok, result, message
        except Exception as e:
            log.error("任务 #%s 执行异常:\n%s", tid, traceback.format_exc())
            return False, None, "任务执行异常: %s" % e

    # ---------------- 查询 ----------------

    def _query(self, reader, name):
        if self.dry_run:
            return True, self._dry_rows(name), "[dry_run] 模拟数据（当前未接入真实券商）"
        data = reader()
        if not data:
            return True, data or [], "查询完成，%s为空" % name
        return True, data, "查询完成，共 %s 条" % len(data) if isinstance(data, list) else "查询完成"

    def _do_query_balance(self, payload):
        if self.dry_run:
            return True, self._dry_balance(), "[dry_run] 模拟数据（当前未接入真实券商）"
        data = dict(self.broker.query_balance() or {})
        if not data:
            return True, {}, "查询完成，但未读取到资金数据（可能被客户端弹窗拦截，请重试）"
        return True, data, "查询完成"

    def _do_query_positions(self, payload):
        return self._query(lambda: list(self.broker.query_positions() or []), "持仓")

    def _do_query_entrusts(self, payload):
        return self._query(lambda: list(self.broker.query_entrusts() or []), "当日委托")

    def _do_query_trades(self, payload):
        return self._query(lambda: list(self.broker.query_trades() or []), "当日成交")

    # ---------------- 交易 ----------------

    def _do_place_order(self, payload):
        security = str(payload.get("security", ""))
        side = str(payload.get("side", ""))
        amount = int(payload.get("amount") or 0)
        price = payload.get("price")

        # 价格留空 -> 用最新价代替（A 股客户端只支持限价委托）
        if price in (None, "", 0):
            price = fetch_last_price(security)
            if price is None:
                return False, None, "未能自动获取 %s 的最新价，请在控制台填写委托价格后重试" % security

        sig = {"security": security, "side": side, "amount": amount, "price": price}
        ok, reason = risk.check(sig, broker=self.broker)
        if not ok:
            log.warning("控制台下单被风控拒绝: %s %s", sig, reason)
            return False, {"security": security, "side": side, "amount": amount, "price": price}, \
                   "风控拒绝: " + reason

        if self.dry_run:
            return True, {"entrust_no": "", "dry_run": True, "price": price}, \
                   "[dry_run] 已通过风控，未真实下单（委托价 %.3f）" % price

        r = (self.broker.buy if side == "buy" else self.broker.sell)(security, price, amount)
        risk._add_daily(amount)   # 走到这里说明已尝试提交，计入当日额度
        result = {"security": security, "side": side, "amount": amount, "price": price,
                  "entrust_no": r.get("entrust_no", "")}
        return bool(r.get("ok")), result, r.get("message", "")

    def _do_cancel_order(self, payload):
        entrust_no = str(payload.get("entrust_no", "")).strip()
        if not entrust_no:
            return False, None, "缺少委托号"
        if self.dry_run:
            return True, {"entrust_no": entrust_no, "dry_run": True}, "[dry_run] 模拟撤单成功"
        r = self.broker.cancel_order(entrust_no)
        return bool(r.get("ok")), {"entrust_no": entrust_no}, r.get("message", "")

    def _do_cancel_all_orders(self, payload):
        if self.dry_run:
            return True, {"dry_run": True}, "[dry_run] 模拟一键全撤成功"
        r = self.broker.cancel_all_orders()
        return bool(r.get("ok")), {"detail": r.get("message", "")}, r.get("message", "")

    # ---------------- 清场 / 重启 / 熔断恢复 ----------------

    def _do_clear_form(self, payload):
        """清场：点[重填]清空下单表单（ths 模式才支持）。"""
        if self.dry_run:
            return True, {}, "[dry_run] 模拟清场成功"
        if config.MODE != "ths":
            return False, None, ("当前模式(%s)不支持清场（清场是同花顺 UI 专用操作）"
                                 % config.MODE)
        r = self.broker.clear_form()
        return bool(r.get("ok")), {"message": r.get("message", "")}, r.get("message", "")

    def _do_restart_ths(self, payload):
        """重启同花顺：**已在运行则重启，未在运行则启动**。

        这是控制台的"远程恢复"按钮，因此刻意不做"券商必须已就绪"的前置检查
        （在 TaskRunner.READY_REQUIRED 里豁免）—— 同花顺没启动/没连上时，
        它恰恰是唯一的自救手段。"""
        if self.dry_run:
            return True, {}, "[dry_run] 模拟重启成功"
        if self.broker is None:
            return False, None, "执行端未加载券商适配器（初始化失败），无法重启同花顺"
        if config.MODE != "ths":
            return False, None, "当前模式非 ths，不支持重启同花顺"
        r = self.broker.restart_ths()
        return bool(r.get("ok")), {"message": r.get("message", "")}, r.get("message", "")

    def _do_guard_reset(self, payload):
        """管理员手动解除 L3 熔断。"""
        if self.guard is not None:
            self.guard.reset()
        return True, {"level": "L0"}, "熔断已由管理员手动恢复"

    # ---------------- dry_run 模拟数据 ----------------

    @staticmethod
    def _dry_balance():
        return {"可用金额": 100000.00, "资金余额": 100000.00, "总资产": 100000.00,
                "可取金额": 0.00, "备注": "dry_run 模拟"}

    @staticmethod
    def _dry_rows(name):
        if name == "持仓":
            return [{"证券代码": "510300", "证券名称": "沪深300ETF(dry_run)", "持仓数量": 100,
                     "可用数量": 100, "成本价": 4.100, "市价": 4.200, "盈亏": 10.00}]
        return []
