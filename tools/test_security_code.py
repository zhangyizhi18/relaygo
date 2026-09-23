# -*- coding: utf-8 -*-
"""
证券代码归一化自测（纯离线，不需要中转服务/券商）。

覆盖：
  A 6 位数字 -> 自动补后缀（股票 / ETF / 可转债 / B 股 / 逆回购 / 科创板 / 创业板）
  B 已带后缀 -> 规范化大小写 + 常见别名（.SH / .SZ / .SS）
  C 后缀与代码矛盾 -> 拒绝（如 510300.XSHE）
  D 不可下单品种 -> 拒绝（北交所 / 指数）
  E 形态错误 -> 拒绝，且保留既有 400 文案的关键字样
  F 幂等性：normalize(normalize(x)) == normalize(x)
  G 前后端规则表一致性：web_ui.py 里的 JQ_T1/T2/T3 必须与 security_code.py 完全一致
  H 与既有市场判定（executor/market_price）在可交易品种上一致（防规则漂移）

用法：
    venv/Scripts/python.exe tools/test_security_code.py
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "relay_server"))
sys.path.insert(0, os.path.join(ROOT, "executor"))

import security_code  # noqa: E402

_n = [0, 0]


def check(name, cond, extra=""):
    _n[0] += 1
    if not cond:
        _n[1] += 1
    print("  [%s] %s %s" % ("OK" if cond else "FAIL", name, extra))


def norm(v):
    return security_code.normalize_security(v)[0]


def err(v):
    return security_code.normalize_security(v)[1]


print("=" * 64)
print("A 6 位数字 -> 自动补后缀")
print("-" * 64)
_A = [
    ("600519", "600519.XSHG", "沪市主板 贵州茅台"),
    ("601398", "601398.XSHG", "沪市主板 工商银行"),
    ("603259", "603259.XSHG", "沪市主板"),
    ("605499", "605499.XSHG", "沪市主板"),
    ("688981", "688981.XSHG", "科创板 中芯国际"),
    ("689009", "689009.XSHG", "科创板 CDR"),
    ("900901", "900901.XSHG", "沪市 B 股"),
    ("510300", "510300.XSHG", "沪市 ETF 沪深300"),
    ("512880", "512880.XSHG", "沪市 ETF 证券"),
    ("588000", "588000.XSHG", "科创50 ETF"),
    ("501050", "501050.XSHG", "沪市 LOF"),
    ("110059", "110059.XSHG", "沪市可转债"),
    ("111000", "111000.XSHG", "沪市可转债"),
    ("113050", "113050.XSHG", "沪市可转债"),
    ("118000", "118000.XSHG", "沪市可转债"),
    ("204001", "204001.XSHG", "沪市国债逆回购 GC001"),
    ("730000", "730000.XSHG", "沪市新股申购"),
    ("000001", "000001.XSHE", "深市主板 平安银行"),
    ("000002", "000002.XSHE", "深市主板 万科A"),
    ("001979", "001979.XSHE", "深市主板"),
    ("002415", "002415.XSHE", "深市中小板 海康威视"),
    ("003816", "003816.XSHE", "深市主板"),
    ("300750", "300750.XSHE", "创业板 宁德时代"),
    ("301236", "301236.XSHE", "创业板"),
    ("159915", "159915.XSHE", "深市 ETF 创业板"),
    ("163208", "163208.XSHE", "深市 LOF"),
    ("184801", "184801.XSHE", "深市基金"),
    ("123456", "123456.XSHE", "深市可转债"),
    ("127056", "127056.XSHE", "深市可转债"),
    ("128136", "128136.XSHE", "深市可转债"),
    ("131810", "131810.XSHE", "深市逆回购 R-001"),
    ("200011", "200011.XSHE", "深市 B 股"),
]
for raw, want, note in _A:
    got = norm(raw)
    check("A %s -> %s" % (raw, want), got == want, "实际 %s（%s）" % (got, note))

print("")
print("=" * 64)
print("B 已带后缀 -> 规范化大小写 / 别名")
print("-" * 64)
for raw, want in [
    ("600519.XSHG", "600519.XSHG"),
    ("600519.xshg", "600519.XSHG"),
    ("000001.xshe", "000001.XSHE"),
    (" 510300.XSHG ", "510300.XSHG"),
    ("600519.SH", "600519.XSHG"),
    ("000001.SZ", "000001.XSHE"),
    ("600519.SS", "600519.XSHG"),
    ("\uff16\uff10\uff10\uff15\uff11\uff19", "600519.XSHG"),   # 全角数字
    ("000001.XSHG", "000001.XSHG"),                          # 上证指数：重码段尊重手填
]:
    got = norm(raw)
    check("B %r -> %s" % (raw, want), got == want, "实际 %s" % got)

print("")
print("=" * 64)
print("C 后缀与代码矛盾 -> 拒绝")
print("-" * 64)
for raw, kw in [
    ("510300.XSHE", "不一致"),
    ("600519.XSHE", "不一致"),
    ("300750.XSHG", "不一致"),
    ("159915.XSHG", "不一致"),
    ("688981.XSHE", "不一致"),
    ("000001.XSHE", None),      # 不矛盾：深市股票
    ("600519.XSHG", None),      # 不矛盾
]:
    e = err(raw)
    if kw is None:
        check("C %s 正常通过" % raw, e is None, "错误=%s" % e)
    else:
        check("C %s 被拒" % raw, e is not None and kw in e, "错误=%s" % e)

print("")
print("=" * 64)
print("D 不可下单品种 -> 拒绝")
print("-" * 64)
for raw, kw in [
    ("830799", "北交所"),
    ("430047", "北交所"),
    ("871981", "北交所"),
    ("920001", "北交所"),
    ("830799.XSHE", "北交所"),
    ("399001", "指数"),
    ("399006", "指数"),
    ("399001.XSHE", "指数"),
]:
    e = err(raw)
    check("D %s 被拒" % raw, e is not None and kw in e, "错误=%s" % e)

print("")
print("=" * 64)
print("E 形态错误 -> 拒绝（保留既有文案字样）")
print("-" * 64)
for raw in ["", None, "   ", "6005", "1234567", "abc", "600519.XSH", "sh600519",
            "6-0-0-5-1-9", "600519.XX", "600519XSHG", "510300.XSHG#"]:
    e = err(raw)
    check("E %r 被拒" % raw, e is not None, "%s" % e[:40])
check("E 形态错误文案保留“格式应为 600519.XSHG”",
      "600519.XSHG" in (err("6005") or ""))
check("E 空值文案", "不能为空" in (err("") or ""))
check("E 未知后缀文案", "后缀" in (err("600519.XX") or ""))

print("")
print("=" * 64)
print("F 幂等性")
print("-" * 64)
for raw in [x[0] for x in _A] + ["600519.SH", "000001.XSHG", "600519.xshg"]:
    once = norm(raw)
    twice = norm(once) if once else None
    check("F %s 幂等" % raw, once == twice, "%s -> %s" % (once, twice))

print("")
print("=" * 64)
print("G 前后端规则表一致性（web_ui.py 的 JQ_T1/T2/T3）")
print("-" * 64)
_ui = open(os.path.join(ROOT, "relay_server", "web_ui.py"), encoding="utf-8").read()


def _js_table(var):
    m = re.search(r"var %s = \{(.*?)\};" % var, _ui, re.S)
    if not m:
        return None
    out = {}
    for k, v in re.findall(r"(\d+)\s*:\s*'([A-Z]*)'", m.group(1)):
        out[k] = v
    return out


def _py_expect(table):
    return {k: ("XSHG" if v[0] == "SH" else "XSHE" if v[0] == "SZ" else "")
            for k, v in table.items()}


for var, py in (("JQ_T3", security_code._T3), ("JQ_T2", security_code._T2),
                ("JQ_T1", security_code._T1)):
    js = _js_table(var)
    check("G %s 可解析" % var, bool(js), "%d 条" % len(js or {}))
    check("G %s 键集合一致" % var, set(js or {}) == set(py),
          "仅前端有=%s 仅后端有=%s" % (sorted(set(js or {}) - set(py)),
                                       sorted(set(py) - set(js or {}))))
    check("G %s 取值一致" % var, js == _py_expect(py),
          "差异=%s" % {k: (js.get(k), _py_expect(py).get(k))
                       for k in set(js or {}) | set(py)
                       if js.get(k) != _py_expect(py).get(k)})

print("")
print("=" * 64)
print("H 与既有市场判定（market_price）在可交易品种上一致")
print("-" * 64)
try:
    import market_price
    _H = ["600519", "601398", "603259", "605499", "688981", "689009", "900901",
          "510300", "512880", "588000", "501050", "110059", "111000", "113050",
          "118000", "730000", "000001", "002415", "003816", "300750", "301236",
          "159915", "163208", "184801", "123456", "127056", "128136", "131810",
          "200011"]
    bad = []
    for c in _H:
        ours = norm(c)
        if not ours:
            bad.append((c, "本模块拒绝", None))
            continue
        tencent = market_price._plain_to_tencent(c)     # sh600519 / sz000001
        if not tencent.startswith("sh" if ours.endswith("XSHG") else "sz"):
            bad.append((c, ours, tencent))
    check("H %d 个常见代码市场判定一致" % len(_H), not bad, "不一致=%s" % bad)
except ImportError as e:
    check("H 跳过（market_price 不可导入）", True, str(e))
# 说明：204xxx（沪逆回购）、4x/8x/920（北交所）不参与 H 比对——
# market_price 的历史规则把它们判成了 sz，本模块已修正，差异是预期的。

print("")
print("=" * 64)
print("结果：通过 %d / %d" % (_n[0] - _n[1], _n[0]))
print("=" * 64)
sys.exit(1 if _n[1] else 0)
