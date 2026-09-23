# -*- coding: utf-8 -*-
"""
市价信号取最新价 —— 仅用标准库，非侵入式新模块（v1.7.1）。

背景：聚宽端市价单（order 不带 limit_price）上报 price=None。
桌面同花顺/安卓模拟器/QMT 的下单接口都需要一个明确价格，
easytrader 拿到 None 会把字面量 "None" 打进价格框或直接崩溃。

这里从腾讯行情接口取最新价（免代理直连，与 jq_signal 同一套做法）。
取不到时返回 None，由调用方决定拒单——宁可不下单，绝不带错价提交。
"""
from urllib.request import ProxyHandler, build_opener

_OPENER = build_opener(ProxyHandler({}))   # 免代理直连（本机调试常被系统代理劫持）


def _plain_to_tencent(code_plain):
    """'600519' -> 'sh600519'；'000001' -> 'sz000001'。按 A 股代码段判断市场。"""
    if code_plain[0] in ("5", "6", "7", "9"):            # 沪：ETF(5xx)/股票(60x,68x)/其他(7xx,9xx)
        return "sh" + code_plain
    if code_plain[0] == "1":                             # 1 开头：看前三位
        if code_plain[:3] in ("110", "111", "113", "118"):   # 沪转债
            return "sh" + code_plain
        return "sz" + code_plain                          # 深转债(123/127/128)、深 ETF(15x/16x/18x)
    return "sz" + code_plain                              # 深：股票(00x,30x)、其他(0/2/3/4/8 开头按深处理)


def fetch_latest(code_plain, timeout=4):
    """
    取最新价。入参为 6 位纯代码（如 '600519'）。
    成功返回 float；失败返回 None（网络异常/代码无行情/解析失败）。
    """
    try:
        code_plain = str(code_plain).strip()
        if not code_plain.isdigit() or len(code_plain) != 6:
            return None
        url = "http://qt.gtimg.cn/q=" + _plain_to_tencent(code_plain)
        req = _OPENER.open(url, timeout=timeout)
        raw = req.read().decode("gbk", "replace")
        # 返回格式: v_sh600519="1~贵州茅台~600519~1520.00~...；最新价是第 4 个字段(下标 3)
        parts = raw.split("~")
        if len(parts) > 3:
            return float(parts[3])
    except Exception:
        return None
    return None
