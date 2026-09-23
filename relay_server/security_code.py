# -*- coding: utf-8 -*-
"""
证券代码归一化（v1.7.1）—— 下单入口统一补全聚宽后缀。

背景：控制台手动下单要求填"聚宽格式" 600519.XSHG，用户常只填 6 位数字，
中转直接 400 拒绝。而后缀唯一的作用是标识沪深——执行端真正下单时只用前
6 位（broker_base.to_plain_code 就是 split(".")[0]），市场完全可由数字段
推导，没必要让人手工填。

原则：**宁拒不错**。判不出来（或判定为不可交易品种）就明确报错让人手填，
绝不猜一个"看起来像"的市场——下错市场是事故，报错只是麻烦。

对外只暴露一个纯函数：
    normalize_security(raw) -> (canonical, error)
        成功: ("600519.XSHG", None)
        失败: (None, "中文原因")
本模块不 import Flask / 数据库 / 任何业务模块，便于单测
（tools/test_security_code.py，纯离线）。

支持的输入形式：
    600519            -> 600519.XSHG
    000001            -> 000001.XSHE
    510300.XSHG       -> 510300.XSHG    （已带后缀，规范化大小写）
    600519.sh / .SZ   -> 600519.XSHG / 000001.XSHE（常见简写别名，顺手支持）
    sh600519 / 7 位 / 含字母  -> 报错（不做前缀猜测，避免误判）
"""

import re
import unicodedata

SUF_SH = "XSHG"
SUF_SZ = "XSHE"

# 后缀别名 -> 内部市场（兼容 .SH / .SZ / .SS 这类常见写法）
_ALIAS = {"XSHG": "SH", "SH": "SH", "SS": "SH", "XSHE": "SZ", "SZ": "SZ"}

# 不可下单的市场/品种（即使格式合法也拒绝，并给出可读原因）
_BLOCKED = {
    "BJ": "北交所（暂不支持，请到券商客户端手工下单）",
    "IDX": "指数（不可交易）",
}

# 前缀表：前缀 -> (market, unique, note)
#   market: SH / SZ / BJ(北交所) / IDX(指数) / None(未知)
#   unique: True=该前缀唯一确定市场（可用来校验用户手写的后缀是否矛盾）
#           False=沪深重码（如 000xxx 既是深市股票又是沪市指数），不判矛盾
# 查表顺序：先 3 位、再 2 位、最后 1 位（最长前缀优先）
_T3 = {
    "688": ("SH", True, "科创板"),
    "689": ("SH", True, "科创板"),
    "900": ("SH", True, "沪市B股"),
    "110": ("SH", True, "沪市可转债"),
    "111": ("SH", True, "沪市可转债"),
    "113": ("SH", True, "沪市可转债"),
    "118": ("SH", True, "沪市可转债"),
    "204": ("SH", True, "沪市国债逆回购"),
    "300": ("SZ", True, "创业板"),
    "301": ("SZ", True, "创业板"),
    "159": ("SZ", True, "深市ETF"),
    "123": ("SZ", True, "深市可转债"),
    "127": ("SZ", True, "深市可转债"),
    "128": ("SZ", True, "深市可转债"),
    "131": ("SZ", True, "深市国债逆回购"),
    "200": ("SZ", True, "深市B股"),
    "399": ("IDX", True, "指数"),
    "430": ("BJ", True, "北交所"),
    "920": ("BJ", True, "北交所"),
}

_T2 = {
    "60": ("SH", True, "沪市主板"),
    "68": ("SH", True, "科创板"),
    "90": ("SH", True, "沪市B股"),
    "50": ("SH", True, "沪市基金"),
    "51": ("SH", True, "沪市ETF"),
    "52": ("SH", True, "沪市基金"),
    "56": ("SH", True, "沪市ETF"),
    "58": ("SH", True, "沪市ETF"),
    "11": ("SH", False, "沪市债券"),     # 112 沪深都有，不判矛盾
    "30": ("SZ", True, "创业板"),
    "15": ("SZ", True, "深市基金"),
    "16": ("SZ", True, "深市基金"),
    "18": ("SZ", True, "深市基金"),
    "12": ("SZ", True, "深市债券"),
    "13": ("SZ", True, "深市逆回购"),
    "20": ("SZ", True, "深市B股"),
    "43": ("BJ", True, "北交所"),
    "83": ("BJ", True, "北交所"),
    "87": ("BJ", True, "北交所"),
}

_T1 = {
    "6": ("SH", True, "沪市"),
    "5": ("SH", True, "沪市基金"),
    "0": ("SZ", False, "深市（000/001 与沪市指数重码）"),
    "3": ("SZ", True, "深市"),
    "1": ("SZ", False, "深市"),
    "2": ("SZ", False, "深市B股"),
    "7": ("SH", True, "沪市申购/配股"),
    "4": ("BJ", True, "北交所"),
    "8": ("BJ", True, "北交所"),
    "9": ("SH", False, "沪市B股/其他"),
}

_TABLES = ((3, _T3), (2, _T2), (1, _T1))

_ERR_FORMAT = ("证券代码格式应为 600519.XSHG / 000001.XSHE，"
               "或直接填 6 位数字（如 600519，系统自动补后缀）")


def _clean(raw):
    """全角转半角、去空白、转大写。"""
    s = unicodedata.normalize("NFKC", str(raw if raw is not None else ""))
    return "".join(s.split()).upper()


def _lookup(code6):
    """按最长前缀返回 (market, unique, note)。"""
    for n, table in _TABLES:
        hit = table.get(code6[:n])
        if hit:
            return hit
    return (None, False, "")


def _canonical(code6, mk):
    return "%s.%s" % (code6, SUF_SH if mk == "SH" else SUF_SZ)


def normalize_security(raw):
    """
    任意常见写法 -> 规范聚宽代码。返回 (canonical, error)，二者必有一为 None。

    >>> normalize_security("600519")
    ('600519.XSHG', None)
    >>> normalize_security("510300")
    ('510300.XSHG', None)
    >>> normalize_security("000001")
    ('000001.XSHE', None)
    >>> normalize_security("000001.XSHG")   # 上证指数：沪深重码段，尊重手填后缀
    ('000001.XSHG', None)
    >>> normalize_security("510300.XSHE")   # 沪市 ETF 写成深市 -> 拒绝
    (None, '...')
    >>> normalize_security("830799")        # 北交所 -> 拒绝
    (None, '...')
    """
    s = _clean(raw)
    if not s:
        return None, "证券代码不能为空"

    m = re.match(r"^(\d{6})(?:\.([A-Z]{2,4}))?$", s)
    if not m:
        return None, _ERR_FORMAT
    code6, suf = m.group(1), m.group(2)
    market, unique, note = _lookup(code6)

    # ---- 已带后缀：规范化 + 矛盾检查 ----
    if suf:
        mk = _ALIAS.get(suf)
        if mk is None:
            return None, ("后缀 .%s 无法识别，请用 .XSHG（沪）/ .XSHE（深）" % suf)
        if market in _BLOCKED:
            return None, "代码 %s 属于%s" % (code6, _BLOCKED[market])
        # 只有"唯一确定市场"的前缀才敢判矛盾（避免误杀 000001.XSHG 上证指数）
        if unique and market in ("SH", "SZ") and mk != market:
            return None, ("代码 %s 属于%s，与后缀 .%s 不一致，请核对该证券所在市场"
                          % (code6, "沪市 XSHG" if market == "SH" else "深市 XSHE", suf))
        return _canonical(code6, mk), None

    # ---- 无后缀：按数字段推导 ----
    if market in _BLOCKED:
        return None, "代码 %s 属于%s" % (code6, _BLOCKED[market])
    if market not in ("SH", "SZ"):
        return None, ("无法自动识别 %s 的市场，请手动填写后缀 .XSHG（沪）/ .XSHE（深）"
                      % code6)
    return _canonical(code6, market), None
