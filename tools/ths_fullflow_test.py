# -*- coding: utf-8 -*-
"""RelayGo 执行端全流程测试（真实模拟账号）

覆盖执行端从"进程起来"到"撤单收尾"的完整链路，并把**故障自愈**也测进去：

  link      执行端是否在线（/api/status 心跳）
  query     查资金 / 持仓 / 当日委托 / 当日成交
  order     真实下单：选一只**可卖持仓**挂高于现价的卖单（不会成交、不占资金）
            -> 核对当日委托字段(代码/方向/价/量/合同编号) -> 一键全撤 -> 核对活委托=0
  selfheal  杀掉交易模块 xiadan.exe（保留行情主程序）-> 执行端应在 ~20 秒内按 F12
            自动重拉并恢复"券商已连接"
  heal_main 连行情主程序 hexin.exe 一起杀掉 -> 执行端应能重新拉起整套并恢复连接

为什么要有 selfheal / heal_main：用户现场反馈"有时候拉不起下单程序，看门狗也没起作用"。
只有把"人为制造客户端消失"作为用例跑一遍，才能证明**自愈链路**真的通，而不是
只在冷启动那一次碰巧成功。

安全设计：
  * 卖单挂价 = 现价 × 1.01（偏离 1%，低于执行端 3% 价格偏差风控线，且高于市价不易成交）
  * 每阶段结束一律全撤，不在账上留挂单
  * 不买入（模拟账号常常现金不足），只测试卖出可卖持仓

判定说明（2026-09-13 实测补）：
  * 下单阶段先看**执行端的真实回报**（/console/api/jq_signal/<id>）。若回报是券商/环境
    原因（如周日收盘后「[120147]当前时间不允许委托」），标 SKIP —— 这不是代码问题，
    不计入失败；只有"回报说成功、当日委托却查不到"才是真 bug（假成功）。
  * 自愈阶段用**真实查资金**判定恢复，不看心跳文案：心跳由独立线程按 slot.ready 的
    旧快照发送，客户端刚被杀时仍会写"券商已连接"，拿它判会假阳性。

用法：
  venv\\Scripts\\python.exe tools\\ths_fullflow_test.py
  venv\\Scripts\\python.exe tools\\ths_fullflow_test.py --phases link,query,order,selfheal
  venv\\Scripts\\python.exe tools\\ths_fullflow_test.py --code 588170 --amount 100
"""
import argparse
import ctypes
import os
import sys
import time
import uuid
from ctypes import wintypes

import requests

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "executor"))

BASE = "http://127.0.0.1:5010"
SIGNAL_KEY = "jq-signal-key-2026-change-me"
ADMIN, ADMIN_PWD = "admin", "admin123"
XIADAN = r"D:\同花顺软件\同花顺\xiadan.exe"
HEXIN = r"D:\同花顺软件\同花顺\hexin.exe"

S = requests.Session()
S.trust_env = False

RESULTS = []          # (阶段, 名称, ok, 说明)
SKIPPED = []          # 因环境限制跳过的项（不计入失败）


def rec(phase, name, ok, detail=""):
    RESULTS.append((phase, name, bool(ok), detail))
    print("  [%s] %-42s %s %s" % ("PASS" if ok else "FAIL", name,
                                  "OK" if ok else "!", detail), flush=True)
    return ok


def skip(phase, name, detail=""):
    """环境限制导致的"测不了"（不算失败，例如非交易时段券商拒单）。"""
    SKIPPED.append((phase, name, detail))
    print("  [SKIP] %-42s - %s" % (name, detail), flush=True)
    return True


def hr(t=''):
    print("\n" + "=" * 78)
    if t:
        print(t)
    print("=" * 78, flush=True)


# ------------------------------------------------------------------ 中转接口
def login():
    r = S.post(BASE + "/console/api/login",
               json={"username": ADMIN, "password": ADMIN_PWD}, timeout=15)
    return r.status_code == 200 and r.json().get("ok"), r.text[:150]


def status():
    try:
        return S.get(BASE + "/api/status", timeout=10).json()
    except Exception as e:
        return {"_err": str(e)}


def heartbeat():
    return (status().get("last_heartbeat") or {}).get("detail") or ""


def account(kind, timeout=90):
    r = S.get(BASE + "/console/api/account", params={"kind": kind}, timeout=timeout)
    j = r.json()
    if not j.get("ok"):
        return None, str(j.get("error") or j.get("message"))[:120]
    return j.get("data") or [], None


def cancel_all(timeout=150):
    try:
        r = S.post(BASE + "/console/api/cancel_all", json={}, timeout=timeout)
        return bool(r.json().get("ok")), r.text[:160]
    except Exception as e:
        return False, str(e)[:160]


def send_signal(code, market, side, amount, price, tag):
    body = {"order_id": "FULL-%s-%s" % (tag, uuid.uuid4().hex[:5]),
            "security": "%s.%s" % (code, market), "side": side,
            "amount": amount, "price": price, "jq_status": "held"}
    r = S.post(BASE + "/api/signal", json=body,
               headers={"X-API-Key": SIGNAL_KEY}, timeout=20)
    ok = r.status_code == 200 and bool(r.json().get("ok"))
    try:
        sid = int(r.json().get("signal_id") or 0)
    except Exception:
        sid = 0
    return ok, sid, r.text[:200]


def signal_detail(sid):
    """单条信号的完整时间线（含执行端回报 feedback）。取不到返回 {}。"""
    if not sid:
        return {}
    try:
        r = S.get(BASE + "/console/api/jq_signal/%d" % sid, timeout=20)
        j = r.json()
        return j if j.get("ok") else {}
    except Exception:
        return {}


def feedback_text(det):
    """把执行端回报拼成一句可读文本（字段名兼容 message/detail/reason）。"""
    parts = []
    for x in (det.get("feedback") or []):
        for k in ("message", "detail", "reason", "msg"):
            v = x.get(k)
            if v:
                parts.append(str(v))
                break
        else:
            parts.append(str({k: v for k, v in x.items()
                              if k in ("ok",)}) + " " + str(x)[:80])
    return " | ".join(parts)[:300]


# 券商/环境"这不是我们代码的错"的拒单特征（非交易时段、非交易用户等）
_ENV_REJECT_HINTS = ("不允许委托", "非交易时段", "不在交易时间", "未开盘",
                     "已收盘", "非交易用户", "休市")


# ------------------------------------------------------------------ 本地进程
k32 = ctypes.windll.kernel32
psapi = ctypes.windll.psapi
psapi.EnumProcesses.argtypes = [ctypes.c_void_p, wintypes.DWORD,
                                ctypes.POINTER(wintypes.DWORD)]
psapi.EnumProcesses.restype = wintypes.BOOL


def pids_of(path):
    arr = (wintypes.DWORD * 2048)()
    need = wintypes.DWORD()
    psapi.EnumProcesses(arr, ctypes.sizeof(arr), ctypes.byref(need))
    n = need.value // ctypes.sizeof(wintypes.DWORD)
    target = os.path.normcase(os.path.abspath(path))
    out = []
    for i in range(n):
        p = arr[i]
        if not p:
            continue
        h = k32.OpenProcess(0x1000 | 0x0400, False, p)
        if not h:
            continue
        try:
            b = ctypes.create_unicode_buffer(1024)
            if psapi.GetModuleFileNameExW(h, None, b, 1024):
                if os.path.normcase(os.path.abspath(b.value)) == target:
                    out.append(p)
        finally:
            k32.CloseHandle(h)
    return out


def kill(path):
    killed = []
    for p in pids_of(path):
        h = k32.OpenProcess(0x0001, False, p)
        if h:
            k32.TerminateProcess(h, 0)
            k32.CloseHandle(h)
            killed.append(p)
    return killed


def num(v, default=-1.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def live_entrusts(rows):
    """活委托（未成交未撤）。备注含"撤"或已成交/已撤完的剔除。"""
    out = []
    for x in rows or []:
        remark = str(x.get("备注") or "")
        if "撤" in remark:
            continue
        qty = num(x.get("委托数量"), 0)
        done = num(x.get("成交数量"), 0)
        cancel = num(x.get("撤消数量"), 0)
        if done + cancel < qty:
            out.append(x)
    return out


# ------------------------------------------------------------------ 各阶段
def phase_link():
    hr("阶段 1/5  链路：执行端是否在线")
    st = status()
    hb = (st.get("last_heartbeat") or {}).get("detail")
    rec("link", "中转 /api/status 可访问", "_err" not in st, str(st.get("version")))
    rec("link", "执行端有上报心跳", bool(hb), str(hb))
    ok = bool(hb) and "券商已连接" in str(hb)
    rec("link", "心跳显示「券商已连接」", ok, str(hb))
    return ok


def phase_query():
    hr("阶段 2/5  查询：资金 / 持仓 / 当日委托 / 当日成交")
    b, e = account("balance")
    rec("query", "查资金", b is not None, str(b)[:110] if b is None else str(b))
    pos, e = account("positions")
    rec("query", "查持仓", pos is not None,
        "%d 只: %s" % (len(pos or []),
                       ", ".join("%s(%s股)" % (x.get("证券代码"), x.get("可用余额"))
                                 for x in (pos or [])[:4])))
    en, e = account("entrusts")
    rec("query", "查当日委托", en is not None, "共 %d 笔" % len(en or []))
    tr, e = account("trades")
    rec("query", "查当日成交", tr is not None, "共 %d 笔" % len(tr or []))
    return pos or []


def phase_order(code, market, amount):
    hr("阶段 3/5  真实下单：挂「可卖持仓」的卖单（不成交）-> 核对 -> 全撤")
    pos, _ = account("positions")
    target = None
    for x in pos or []:
        if str(x.get("证券代码", "")).startswith(code):
            target = x
            break
    if not target:
        rec("order", "找到可卖持仓 %s" % code, False, "持仓里没有该代码，跳过下单")
        return False
    avail = int(num(target.get("可用余额"), 0))
    rec("order", "持仓可卖数量充足", avail >= amount,
        "%s 可卖 %d 股（本单 %d 股）" % (code, avail, amount))
    if avail < amount:
        return False

    try:
        import market_price
        latest = market_price.fetch_latest(code)
    except Exception:
        latest = None
    if not latest or latest <= 0:
        latest = num(target.get("市价"))
    price = int(float(latest) * 1.01 * 1000) / 1000.0
    print("  现价 %s -> 委托价 %s（+1%%，低于 3%% 价格偏差风控线）" % (latest, price), flush=True)

    before, _ = account("entrusts")
    before_no = {str(x.get("合同编号")) for x in (before or [])}

    st0 = status()
    base_acc = int(st0.get("accepted", 0)) + int(st0.get("failed", 0))
    ok, sid, msg = send_signal(code, market, "sell", amount, price, "ORDER")
    rec("order", "聚宽信号上报被受理", ok, msg)

    # 等执行端处理（看计数增量，不频繁查委托以免触发验证码）
    deadline = time.time() + 180
    done = 0
    while time.time() < deadline:
        time.sleep(3)
        cur = status()
        done = int(cur.get("accepted", 0)) + int(cur.get("failed", 0)) - base_acc
        if done >= 1:
            break
    rec("order", "执行端已处理该笔信号", done >= 1, "计数增量 %d" % done)

    # 先看执行端的**真实回报**，再决定后面怎么判：
    #   * 回报里是券商/环境原因（非交易时段等）→ 这不是代码问题，标 SKIP 跳过；
    #   * 回报说成功但当日委托里查不到 → 那才是"假成功"，必须 FAIL。
    det = signal_detail(sid)
    fb = feedback_text(det)
    if fb and any(k in fb for k in _ENV_REJECT_HINTS):
        skip("order", "下单核对（环境限制：券商按非交易时段拒单）", fb)
        return True

    time.sleep(2)
    after, e = account("entrusts")
    if after is None:
        rec("order", "查当日委托核对", False, str(e))
        return False
    new = [x for x in after if str(x.get("合同编号")) not in before_no]
    print("  本轮新增委托 %d 笔：" % len(new), flush=True)
    for x in new:
        print("    #%s %s %s %s x%s 备注=%s"
              % (x.get("合同编号"), x.get("证券代码"), x.get("操作"),
                 x.get("委托价格"), x.get("委托数量"), x.get("备注")), flush=True)
    hit = None
    for x in new:
        if str(x.get("证券代码", "")).startswith(code):
            hit = x
            break
    rec("order", "当日委托里出现该笔（代码匹配）", hit is not None,
        "" if hit is not None else "执行端回报=%s" % (fb or "（无）"))
    if hit:
        side_ok = "卖" in str(hit.get("操作") or "")
        p_ok = abs(num(hit.get("委托价格")) - price) < 1e-6
        a_ok = int(num(hit.get("委托数量"))) == amount
        rec("order", "字段核对：方向=卖", side_ok, str(hit.get("操作")))
        rec("order", "字段核对：价格=%s" % price, p_ok, str(hit.get("委托价格")))
        rec("order", "字段核对：数量=%s" % amount, a_ok, str(hit.get("委托数量")))
        rec("order", "拿到合同编号", bool(hit.get("合同编号")), str(hit.get("合同编号")))

    lv = live_entrusts(after)
    rec("order", "撤单前存在活委托", len(lv) >= 1, "%d 笔" % len(lv))

    ok, msg = cancel_all()
    rec("order", "一键全撤返回成功", ok, msg)
    time.sleep(2)
    after2, _ = account("entrusts")
    lv2 = live_entrusts(after2)
    rec("order", "全撤后活委托 = 0", len(lv2) == 0, "剩余 %d 笔" % len(lv2))
    return True


def _wait_connected(timeout, label):
    """轮询等待执行端"真的恢复了"。

    为什么不能只看 /api/status 的心跳文案（2026-09-13 实测踩坑）：
      心跳由**独立线程**发送，值取自 slot.ready 的**上一次快照**。杀掉同花顺后，
      主循环往往要等下一次 health_check 失败才把 slot.ready 置 False —— 这中间
      查询接口其实已经不可用，心跳却还写着"券商已连接"。于是 _wait_connected 会
      **立刻返回"0 秒后恢复"**，是假阳性（本次实测就是这样，害得 selfheal/heal_main
      看起来全过、实际什么都没验证）。

    正确判据：**真的去查一次账户**（account()）。它必须经过"执行端 → easytrader →
      同花顺"整条链路，只有连接真的回来了才可能成功。
    """
    t0 = time.time()
    last = ""
    while time.time() - t0 < timeout:
        b, e = account("balance", timeout=45)
        if b is not None:
            return True, "%.0f 秒后恢复（真实查资金成功，心跳=%s）" % (time.time() - t0, heartbeat())
        last = str(e)
        time.sleep(5)
    return False, "%s 秒内未能恢复真实查询，最后错误=%s（心跳=%s）" % (timeout, last[:80], heartbeat())


def phase_selfheal():
    hr("阶段 4/5  自愈：杀掉交易模块 xiadan.exe（保留行情主程序）")
    killed = kill(XIADAN)
    rec("selfheal", "已结束 xiadan.exe", bool(killed), "pid=%s" % killed)
    time.sleep(3)
    b, e = account("balance", timeout=60)
    rec("selfheal", "杀后立即查资金应失败或重连中", True,
        "查询结果: %s" % ("成功(可能已重连)" if b is not None else "失败: %s" % e))
    # 超时给 240 秒：实测"客户端被杀"瞬间主循环可能被 pywinauto 阻塞 1~2 分钟
    # （心跳仍写"已连接"，属陈旧值），随后才开始重拉，见 _wait_connected 的说明。
    ok, msg = _wait_connected(240, "selfheal")
    rec("selfheal", "执行端自动重拉交易模块并恢复真实查询", ok, msg)
    return ok


def phase_heal_main():
    hr("阶段 5/5  深度自愈：连行情主程序 hexin.exe 一起杀掉")
    k1 = kill(XIADAN)
    k2 = kill(HEXIN)
    rec("heal_main", "已结束 xiadan.exe", bool(k1), "pid=%s" % k1)
    rec("heal_main", "已结束 hexin.exe", bool(k2), "pid=%s" % k2)
    ok, msg = _wait_connected(360, "heal_main")
    rec("heal_main", "执行端重新拉起整套并恢复真实查询", ok, msg)
    if ok:
        b, e = account("balance")
        rec("heal_main", "恢复后查资金可用", b is not None, str(b)[:110])
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phases", default="link,query,order,selfheal,heal_main",
                    help="要跑的阶段（逗号分隔）: link,query,order,selfheal,heal_main")
    ap.add_argument("--code", default="588170", help="下单用代码（默认取持仓里第一只可卖）")
    ap.add_argument("--market", default="XSHG")
    ap.add_argument("--amount", type=int, default=100)
    a = ap.parse_args()
    phases = [p.strip() for p in a.phases.split(",") if p.strip()]

    hr("RelayGo 执行端全流程测试")
    print("  阶段   %s" % ", ".join(phases))
    ok, msg = login()
    print("  控制台登录 %s %s" % (ok, msg))
    if not ok:
        return 2

    code, market = a.code, a.market
    if "order" in phases:
        pos, _ = account("positions")
        if pos and code:
            for x in pos:
                if str(x.get("证券代码", "")).startswith(code):
                    market = "XSHG" if "上海" in str(x.get("交易市场", "")) else "XSHE"
                    break

    t0 = time.time()
    if "link" in phases:
        phase_link()
    if "query" in phases:
        phase_query()
    if "order" in phases:
        phase_order(code, market, a.amount)
    if "selfheal" in phases:
        phase_selfheal()
    if "heal_main" in phases:
        phase_heal_main()

    hr("汇总（耗时 %.0f 秒）" % (time.time() - t0))
    npass = sum(1 for r in RESULTS if r[2])
    for ph in phases:
        rows = [r for r in RESULTS if r[0] == ph]
        if not rows:
            continue
        p = sum(1 for r in rows if r[2])
        print("  %-10s %d/%d" % (ph, p, len(rows)))
    print("  %-10s %d/%d" % ("合计", npass, len(RESULTS)))
    if SKIPPED:
        print("\n  环境限制跳过（不算失败）：")
        for ph, name, d in SKIPPED:
            print("    [%s] %s  %s" % (ph, name, d))
    bad = [r for r in RESULTS if not r[2]]
    if bad:
        print("\n  失败项：")
        for ph, name, ok, d in bad:
            print("    [%s] %s  %s" % (ph, name, d))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
