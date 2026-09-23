# -*- coding: utf-8 -*-
"""
风控模块 —— 信号到达执行端后、真正下单前的最后一道闸。
任何一条不通过都拒绝下单并把原因写进回报，绝不"猜着下单"。
"""
import datetime
import logging
import threading

import config
from broker_base import market_of, to_plain_code

log = logging.getLogger("executor")

_lock = threading.Lock()
_today_traded_amount = {"date": None, "total": 0}


def _today_str():
    return datetime.date.today().strftime("%Y-%m-%d")


def _add_daily(amount):
    """累计当日下单股数（含被拒绝的？不含，只在真正提交后调用）。"""
    with _lock:
        if _today_traded_amount["date"] != _today_str():
            _today_traded_amount["date"] = _today_str()
            _today_traded_amount["total"] = 0
        _today_traded_amount["total"] += amount
        return _today_traded_amount["total"]


def _held_amount(positions, code_plain):
    """从持仓列表里取该代码的**可卖数量**（取不到返回 0）。

    2026-09-13 修复（实测踩到的真 bug）：原实现写的是
        held = sum(int(p.get("amount", 0) or 0) for p in positions if ...)
    而本项目的 broker_ths.query_positions() 直接返回 easytrader 的原始行，
    列名是**中文**（证券代码 / 股票余额 / 可用余额 …），根本没有 `amount` 键
    —— 于是 held 恒为 0，**所有卖出信号都被自家风控拒掉**，回报写
    "卖出 N 股但实际持仓仅 0 股"，而控制台查持仓明明有货
    （现场：588170 可卖 15300 股，卖 100 股仍被拒）。
    这里做字段名兼容；优先"可用余额"（T+1 下当天买入不可卖，用可卖量判断更准），
    没有可卖字段才退回总持仓。
    """
    total = 0
    for p in positions or []:
        try:
            code = str(p.get("security") or p.get("证券代码") or p.get("代码") or "")
            if not code or code_plain not in code:
                continue
            for key in ("可用余额", "可卖数量", "可用数量", "enable_amount",
                        "可用股份", "amount", "股票余额", "当前持仓", "持仓数量"):
                v = p.get(key)
                if v in (None, ""):
                    continue
                try:
                    total += int(float(v))
                except (TypeError, ValueError):
                    continue
                break          # 每行只取第一个命中的字段，避免重复累加
        except Exception:
            continue
    return total


def check(sig, broker=None, last_price=None):
    """
    下单前校验。sig 为中转信号 dict，last_price 为最新价（可为 None 则跳过偏差校验）。
    返回 (ok, reason)。
    """
    code_plain = to_plain_code(sig["security"])
    side = sig["side"]
    amount = int(sig["amount"])
    price = sig.get("price")

    # 1. 白名单
    if config.CODE_WHITELIST and code_plain not in config.CODE_WHITELIST:
        return False, "代码 %s 不在白名单内" % code_plain

    # 2. 市场识别（沪深以外暂不支持，如北交 8xxxxx 需要另行确认）
    if market_of(sig["security"]) is None:
        return False, "无法识别市场（仅支持 .XSHG/.XSHE），代码: %s" % sig["security"]

    # 3. 数量合法性
    if amount <= 0:
        return False, "数量非法: %s" % amount
    if amount > config.MAX_SINGLE_AMOUNT:
        return False, "单笔 %s 股超过上限 %s" % (amount, config.MAX_SINGLE_AMOUNT)

    # 4. 当日累计限额
    with _lock:
        if _today_traded_amount["date"] == _today_str() and \
           _today_traded_amount["total"] + amount > config.MAX_DAILY_AMOUNT:
            return False, ("当日累计 %s 股 + 本笔 %s 股将超过日限额 %s，"
                           "已暂停交易" % (_today_traded_amount["total"],
                                          amount, config.MAX_DAILY_AMOUNT))

    # 5. 卖出必须有持仓（买入校验资金交给券商自己拒）
    if side == "sell" and broker is not None:
        try:
            positions = broker.query_positions()
            held = _held_amount(positions, code_plain)
            if held < amount:
                return False, "卖出 %s 股但实际可卖仅 %s 股" % (amount, held)
        except Exception as e:
            return False, "查询持仓失败，为安全起见拒绝卖出: %s" % e

    # 6. 价格偏差（信号带显式价格时才校验：对比实时最新价）
    #    2026-09-13 修复：原实现依赖调用方传 last_price，而信号/控制台两条路径
    #    都没传，导致校验从未生效——回测历史价(如 16.21 vs 现价 7.74)一路放行
    #    到同花顺才被拒。现在这里自己取实时价兜底。
    if price:
        if not last_price:
            try:
                import market_price
                last_price = market_price.fetch_latest(to_plain_code(sig["security"]))
            except Exception as e:
                log.warning("价格偏差校验取实时价失败（跳过校验）: %s", e)
                last_price = None
        if last_price:
            try:
                dev = abs(float(price) - float(last_price)) / float(last_price)
                if dev > config.MAX_PRICE_DEVIATION:
                    return False, ("信号价 %.3f 偏离最新价 %.3f 达 %.1f%%，超过 %.1f%% 阈值"
                                   % (float(price), float(last_price), dev * 100,
                                      config.MAX_PRICE_DEVIATION * 100))
            except (TypeError, ValueError):
                pass

    return True, "ok"
