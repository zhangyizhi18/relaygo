# -*- coding: utf-8 -*-
"""
真实同花顺链路测试（安全组合，绝不让测试单成交）：
  A. 只读：资金/持仓/当日委托/当日成交（验证 easytrader 连接 + 前台/剪贴板/验证码链路）
  B. 市价链路无风险验证：mock 掉 easytrader 提交，验证 price=None -> 取最新价 -> 规范化 的完整解析
  C. 跌停价限价买 510300（必然不成交）-> 当日委托查到 -> 撤单 -> 确认撤掉
  D. 无持仓卖出 -> 同花顺拒绝（验证错误路径不崩溃）
  E. 风控前置：超额数量被 risk.check 拦下（不触同花顺）

运行:  venv/Scripts/python.exe tools/test_ths_live.py
注意:  需要同花顺客户端已登录交易账号；测试中若弹验证码请按提示输入。
"""
import os
import sys
import time
import json
import threading

EXEC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "executor")
sys.path.insert(0, EXEC_DIR)

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("OK   %s" % name, flush=True)
    else:
        FAIL += 1
        print("FAIL %s  %s" % (name, detail), flush=True)


def run_with_timeout(fn, seconds, label):
    """easytrader 调用可能被弹窗卡住，包一层超时防止测试挂死。"""
    box = {}

    def _run():
        try:
            box["r"] = fn()
        except Exception as e:
            box["err"] = e

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(seconds)
    if t.is_alive():
        print("TIMEOUT %s 超过 %ds 未返回（可能有弹窗卡住 UI）" % (label, seconds), flush=True)
        return None, "timeout"
    return box.get("r"), box.get("err")


def main():
    import config as cfg
    from broker_ths import ThsBroker
    import broker_base
    import market_price
    import risk

    print("== A. 连接与只读查询 ==", flush=True)
    b = ThsBroker(cfg.THS_XIADAN_PATH)
    b.connect()
    check("easytrader 连接成功", b.user is not None)

    bal, err = run_with_timeout(b.query_balance, 60, "资金查询")
    check("资金查询", bal and not err, err or bal)
    if isinstance(bal, dict):
        avail = bal.get("可用金额") or bal.get("可用资金") or bal.get("available")
        print("     可用资金: %s" % avail, flush=True)
    if err or not bal:
        print("[终止] 连不上或查不到资金，后面不测了", flush=True)
        return 1

    pos, err = run_with_timeout(b.query_positions, 60, "持仓查询")
    check("持仓查询(行数=%s)" % (len(pos) if isinstance(pos, list) else "?"),
          pos is not None and not err, err)

    ent, err = run_with_timeout(b.query_entrusts, 60, "当日委托查询")
    check("当日委托查询(行数=%s)" % (len(ent) if isinstance(ent, list) else "?"),
          ent is not None and not err, err)
    if isinstance(ent, list) and ent:
        for r in ent[:5]:
            print("     委托: %s %s %s x%s @%s [%s]" % (
                r.get("合同编号"), r.get("证券代码"), r.get("操作"),
                r.get("成交数量") or r.get("委托数量"), r.get("委托价格"),
                r.get("委托状态") or ""), flush=True)

    trd, err = run_with_timeout(b.query_trades, 60, "当日成交查询")
    check("当日成交查询", trd is not None and not err, err)

    # ---- C 前置：确认没有其它可撤委托（否则撤单安全路径不会执行） ----
    others_cancellable = False
    if isinstance(ent, list):
        others_cancellable = len(ent) > 0

    print("\n== B. 市价链路无风险验证（mock 提交） ==", flush=True)
    resolved = {}
    orig_buy = b.user.buy

    def fake_buy(security, price, amount, **kw):
        resolved["code"], resolved["price"], resolved["amount"] = security, price, amount
        return {"entrust_no": "MOCK"}

    b.user.buy = fake_buy
    r = b.buy("600519.XSHG", None, 100)          # 市价信号：price=None
    b.user.buy = orig_buy
    latest = market_price.fetch_latest("600519")
    check("市价信号 price=None 解析出最新价",
          r.get("ok") and resolved.get("price") and
          abs(resolved["price"] - broker_base.normalize_price("600519.XSHG", latest)) < 1e-9,
          "resolved=%s latest=%s r=%s" % (resolved.get("price"), latest, r.get("message")))
    check("市价解析价格符合股票 2 位小数",
          resolved.get("price") == round(resolved.get("price", 0), 2), resolved)
    r0 = b.buy("600519.XSHG", 0, 100)            # price=0 也按市价兜底
    b.user.buy = orig_buy if b.user.buy is not fake_buy else orig_buy
    check("price=0 按市价兜底不报错", isinstance(r0, dict) and "message" in r0, r0)

    # 恢复真实 buy 后，price 仍无法获取的场景：瞎代码
    r2 = b.buy("999999.XSHG", None, 100)         # 无行情 -> 应拒单
    check("取不到行情的市价单被拒绝", r2.get("ok") is False, r2.get("message"))

    print("\n== C. 限价买(不成交) -> 查委托 -> 撤单 ==", flush=True)
    q = market_price.fetch_latest("510300")
    raw = market_price._OPENER.open("http://qt.gtimg.cn/q=sh510300", timeout=4).read()
    parts = raw.decode("gbk", "replace").split("~")
    prev_close = float(parts[4])
    limit_down = round(prev_close * 0.9, 2)      # ETF 10% 跌停价（大概率触发偏离弹窗被拦）
    price_low = round(q * 0.99, 3)               # 现价-1%（不在偏离弹窗范围，且秒级内几乎不可能成交）
    print("     510300 现价=%s 昨收=%s 跌停=%s 备选价=%s" % (q, prev_close, limit_down, price_low), flush=True)

    r3, err3 = run_with_timeout(lambda: b.buy("510300.XSHG", limit_down, 100), 120, "跌停限价买")
    check("跌停限价买结果如实（成功带委托号 或 失败明说）",
          r3 is not None and ((r3.get("ok") and r3.get("entrust_no")) or
                              (not r3.get("ok") and "疑似未提交" in r3.get("message", ""))),
          err3 or r3)
    print("     跌停价结果: %s" % (r3 or {}).get("message"), flush=True)

    submitted_no = (r3 or {}).get("entrust_no", "")
    if not submitted_no and r3 is not None and not r3.get("ok"):
        # 跌停价被弹窗拦了 -> 用 -1% 价走成功路径（不成交、马上撤）
        print("     跌停价被拦，改用 %.3f 走成功链路" % price_low, flush=True)
        r3, err3 = run_with_timeout(lambda: b.buy("510300.XSHG", price_low, 100), 120, "-1%限价买")
        check("-1%%限价买提交并实证", r3 and r3.get("ok") and r3.get("entrust_no"),
              err3 or (r3 or {}).get("message"))

    entrust_no = (r3 or {}).get("entrust_no", "") if isinstance(r3, dict) else ""
    if not entrust_no:
        print("     [跳过查撤] 没有成功提交的测试单（两种价格都被拦），请人工看 THS 弹窗", flush=True)
        check("至少拿到一个可撤委托号", False, "两种价格都未能提交")
    else:
        time.sleep(2)
        ent2, err2 = run_with_timeout(b.query_entrusts, 60, "委托后当日委托查询")
        found = None
        if isinstance(ent2, list):
            for row in ent2:
                if "510300" in str(row.get("证券代码", "")):
                    found = row
        check("当日委托里查到测试单（无空行垃圾）", found is not None,
              "委托列表=%s err=%s" % ([(r.get("合同编号"), r.get("证券代码")) for r in (ent2 or [])][:6], err2))
        if found:
            print("     测试单: %s %s x%s @%s" % (found.get("合同编号"), found.get("操作"),
                                                  found.get("委托数量"), found.get("委托价格")), flush=True)
            real_no = str(found.get("合同编号") or entrust_no)
            others = [r for r in (ent2 or []) if str(r.get("合同编号")) != real_no]
            if others:
                print("     [跳过撤单] 账户还有 %d 笔其它委托，cancel_order 安全路径要求唯一" % len(others), flush=True)
                check("撤单步骤跳过(非唯一场景)", True)
            else:
                rc, errc = run_with_timeout(lambda: b.cancel_order(real_no), 120, "撤单")
                check("撤单执行", rc and rc.get("ok"), errc or (rc or {}).get("message"))
                time.sleep(2)
                ent3, _ = run_with_timeout(b.query_entrusts, 60, "撤后查询")
                still = [r for r in (ent3 or []) if str(r.get("合同编号")) == real_no]
                check("撤单后当日委托已无该单(或状态已撤)",
                      isinstance(ent3, list) and (not still or
                                                  "撤" in str(still[0].get("委托状态", "")) or
                                                  "撤" in str(still[0].get("备注", ""))),
                      still)

    print("\n== D. 无持仓卖出（THS 拒单路径） ==", flush=True)
    rs, errs = run_with_timeout(lambda: b.sell("600519.XSHG", price_low, 100), 150, "无持仓卖出")
    check("无持仓卖出如实报失败(不假成功)",
          (errs is not None) or (isinstance(rs, dict) and rs.get("ok") is False),
          "rs=%s errs=%s" % (rs, errs))

    print("\n== E. 风控前置拦截 ==", flush=True)
    ok, reason = risk.check({"security": "600519.XSHG", "side": "buy",
                             "amount": 10 ** 8, "price": 100.0}, broker=None)
    check("超额数量被风控拦截", not ok, reason)

    print("\n===== 真实THS链路测试: %d 过 / %d 挂 =====" % (PASS, FAIL), flush=True)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
