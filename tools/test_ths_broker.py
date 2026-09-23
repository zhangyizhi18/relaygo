# -*- coding: utf-8 -*-
"""
同花顺客户端（easytrader 方案A）全自动分级测试。

用法：
  python tools/test_ths_broker.py            # 完整流程：只读测试 -> 挂单演练 -> 撤单验证
  python tools/test_ths_broker.py readonly   # 只做只读测试（查资金/持仓/委托）
  python tools/test_ths_broker.py cancel 510300   # 撤掉指定代码的所有未成交委托（应急工具）

安全设计（重要）：
  - 挂单演练选用【场内ETF + 远低于现价50%的限价买单】：
      · ETF 门槛低（100份 约 一两百元），占用资金极少
      · 价格远低于市价 => 排在队列外，几乎不可能成交，测完即撤单
  - 下单前校验可用资金充足；给出 5 秒倒计时，期间 Ctrl+C 可中止
"""
import sys
import time
from datetime import datetime

sys.path.insert(0, r"<项目根目录>\executor")
from foreground import focus_ths_window   # noqa: E402
import pywinauto_compat   # noqa: E402,F401  SendInput逐事件补丁，必须在 easytrader 之前导入

# ==================== 配置 ====================
XIADAN_PATH = r"D:\同花顺软件\同花顺\xiadan.exe"
TEST_CODE = "510300"          # 测试标的：沪深300ETF(510300)，请确保账户能交易ETF
TEST_AMOUNT = 100             # 100份 = 1手，最小单位
ORDER_PRICE_RATIO = 0.92      # 挂单价 = 现价 * 92%（约 -8%）：合法（在±10%涨跌停内）且几乎不可能成交
# =============================================

results = []


def step(name, ok, detail=""):
    tag = "PASS" if ok else "FAIL"
    print("  [%s] %s %s" % (tag, name, detail))
    results.append((name, ok, detail))
    return ok


def get_live_price(code):
    """腾讯行情接口取现价（免费，无需注册）。"""
    import requests
    mkt = "sh" if code.startswith(("6", "5")) else "sz"
    url = "http://qt.gtimg.cn/q=%s%s" % (mkt, code)
    r = requests.get(url, timeout=5)
    text = r.content.decode("gbk", errors="ignore")
    fields = text.split("~")
    if len(fields) < 4:
        raise RuntimeError("行情解析失败: " + text[:100])
    name, price = fields[1], float(fields[3])
    return name, price


def main_readonly(user):
    print("\n========== 阶段A：只读测试 ==========")
    ok, hwnd = focus_ths_window()
    print("  [INFO] 窗口置前: %s (hwnd=%s)" % ("成功" if ok else "失败", hwnd))
    try:
        bal = user.balance
        step("查询资金", True, str(dict(bal))[:160])
    except Exception as e:
        return step("查询资金", False, str(e)[:200])

    try:
        pos = user.position
        step("查询持仓", True, "%d 条持仓" % len(pos or []))
    except Exception as e:
        step("查询持仓", False, str(e)[:200])

    try:
        user.refresh()
        entrusts = user.today_entrusts
        step("查询当日委托", True, "%d 条委托" % len(entrusts or []))
    except Exception as e:
        step("查询当日委托", False, str(e)[:200])

    try:
        trades = user.today_trades
        step("查询当日成交", True, "%d 条成交" % len(trades or []))
    except Exception as e:
        step("查询当日成交", False, str(e)[:200])
    return True


def find_entrust(entrusts, code, exclude_status=("已撤", "废单", "全部成交")):
    """在当日委托里找指定代码、未撤单的一条。"""
    for e in entrusts or []:
        values = [str(v) for v in e.values()]
        joined = "|".join(values)
        if code in joined:
            status = str(e.get("状态", e.get("委托状态", "")))
            if status not in exclude_status:
                no = e.get("合同编号", e.get("委托编号", ""))
                return no, status
    return None, None


def main_order_test(user):
    print("\n========== 阶段B：真实挂单演练（远低于市价 + 自动撤单） ==========")
    ok, hwnd = focus_ths_window()
    print("  [INFO] 窗口置前: %s (hwnd=%s)" % ("成功" if ok else "失败", hwnd))
    # 1. 取现价
    try:
        name, cur = get_live_price(TEST_CODE)
        step("获取实时行情", True, "%s(%s) 现价 %.3f" % (name, TEST_CODE, cur))
    except Exception as e:
        return step("获取实时行情", False, str(e)[:200])

    limit_price = round(cur * ORDER_PRICE_RATIO, 3)   # ETF 最小变动 0.001
    print("  计划挂单价: %.3f（现价的 %.0f%%，远低于市价，不会成交）"
          % (limit_price, ORDER_PRICE_RATIO * 100))

    # 2. 资金校验
    need = limit_price * TEST_AMOUNT
    try:
        bal = dict(user.balance)
        avail = 0.0
        for k, v in bal.items():
            if "可用" in str(k):
                try:
                    avail = float(v)
                    break
                except (TypeError, ValueError):
                    pass
        if avail < need * 1.5:
            return step("可用资金校验", False,
                        "可用 %.2f 不足挂单所需 %.2f 的 1.5 倍，终止演练" % (avail, need))
        step("可用资金校验", True, "可用 %.2f，挂单约需 %.2f" % (avail, need))
    except Exception as e:
        return step("可用资金校验", False, str(e)[:200])

    # 3. 倒计时确认
    print("  >>> 5 秒后将向同花顺提交真实买单：%s %s x%d @%.3f <<<" %
          (name, TEST_CODE, TEST_AMOUNT, limit_price))
    print("  （不会成交：价格远低于市价；如需中止请按 Ctrl+C）")
    for i in range(5, 0, -1):
        print("    %d..." % i)
        time.sleep(1)

    # 4. 挂单
    try:
        r = user.buy(security=TEST_CODE, price=limit_price, amount=TEST_AMOUNT)
        time.sleep(2)
        user.refresh()
        print("  buy() 返回:", str(r)[:120])
        step("提交限价买单", True, "已通过客户端提交")
    except Exception as e:
        return step("提交限价买单", False, str(e)[:300])

    # 5. 核对：优先查当日委托列表；查不到时用"资金冻结≈委托金额"兜底
    #    （实测：委托刚提交时列表可能读不到，但资金会立刻冻结 price*amount+佣金）
    entrust_no = None
    for _ in range(3):
        try:
            focus_ths_window()
            user.refresh()
            no, status = find_entrust(user.today_entrusts, TEST_CODE)
            if no:
                entrust_no = no
                break
        except Exception:
            time.sleep(2)

    frozen = 0.0
    try:
        bal2 = dict(user.balance)
        total = float(bal2.get("资金余额", 0) or 0)
        avail = float(bal2.get("可用金额", 0) or 0)
        frozen = round(total - avail, 2)
    except Exception:
        pass

    if entrust_no:
        step("委托核对", True, "委托单号 %s 已出现在当日委托中" % entrust_no)
    elif frozen >= need and frozen <= need * 1.05:
        # 买 510300 冻结 420.43 = 420.30 + 0.13 佣金，属于正常范围
        entrust_no = str(r.get("entrust_no")) if isinstance(r, dict) and r.get("entrust_no") else "未知(资金冻结佐证)"
        step("委托核对", True,
             "列表暂未读到，但资金已冻结 %.2f 元 ≈ 委托金额，单子已生效（单号 %s）"
             % (frozen, entrust_no))
    else:
        step("委托核对", False,
             "委托列表未找到且无资金冻结（冻结=%.2f），请人工检查客户端！" % frozen)
        return False

    # 6. 撤单（实测安全路径：读撤单表 -> 确认目标唯一 -> 点[全撤]）
    try:
        focus_ths_window()
        rows = user.cancel_entrusts
        matching = [r for r in (rows or [])
                    if str(r.get("合同编号")) == entrust_no
                    or str(r.get("证券代码")) == TEST_CODE]
        if len(rows or []) == 1 and len(matching) == 1:
            user.cancel_all_entrusts()
            step("提交撤单", True, "已通过[全撤]撤销委托（%s）" % entrust_no)
        else:
            return step("提交撤单", False,
                        "撤单页可撤%d行/匹配%d行，非唯一，请人工撤销 %s"
                        % (len(rows or []), len(matching), entrust_no))
    except Exception as e:
        return step("提交撤单", False,
                    str(e)[:250] + "（请人工撤单！委托单号 %s）" % entrust_no)

    # 7+8. 撤单确认：以资金解冻为准（冻结差值归零即撤单成功）
    frozen_ok = False
    for _ in range(4):
        time.sleep(3)
        try:
            user.refresh()
            bal = dict(user.balance)
            avail = 0.0
            total = 0.0
            for k, v in bal.items():
                if "可用" in str(k):
                    try:
                        avail = float(v)
                    except (TypeError, ValueError):
                        pass
                if "资金余额" in str(k) or k == "总资产":
                    try:
                        total = float(v)
                    except (TypeError, ValueError):
                        pass
            # 冻结差值 = 资金余额 - 可用（撤单成功后归零）
            if avail >= total - 0.01:
                frozen_ok = True
                break
        except Exception:
            pass
    step("撤单确认(资金解冻)", frozen_ok,
         "可用 %.2f / 资金余额 %.2f" % (avail, total) if frozen_ok
         else "资金仍有冻结，请人工确认委托 %s 状态" % entrust_no)
    return True


def main():
    import easytrader
    print("=" * 60)
    print("同花顺客户端全自动测试  %s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("客户端: %s" % XIADAN_PATH)
    print("=" * 60)

    # 连接
    try:
        user = easytrader.use("universal_client")
        user.grid_strategy = easytrader.grid_strategies.Copy   # 剪贴板读表格（新版同花顺必需）
        user.connect(XIADAN_PATH)
        user.enable_type_keys_for_editor()
        print("[OK] 已连接同花顺下单窗口")
    except Exception as e:
        print("[FAIL] 连接失败: %s" % e)
        print("排查: 1) xiadan.exe 路径 2) 客户端已登录交易 3) 用管理员运行本脚本")
        return 1

    # 只读
    readonly_ok = main_readonly(user)
    if not readonly_ok or len(sys.argv) > 1 and sys.argv[1] == "readonly":
        pass
    elif sys.argv[1:2] == ["cancel"]:
        code = sys.argv[2] if len(sys.argv) > 2 else TEST_CODE
        user.refresh()
        entrusts = user.today_entrusts
        n = 0
        for e in entrusts or []:
            no, status = find_entrust([e], code)
            if no:
                try:
                    user.cancel_entrust(no)
                    n += 1
                    print("  已撤: %s" % no)
                except Exception as ex:
                    print("  撤单失败 %s: %s" % (no, ex))
        print("共撤销 %d 条 %s 的委托" % (n, code))
        return 0
    else:
        main_order_test(user)

    # 汇总
    print("\n========== 测试汇总 ==========")
    fail = [r for r in results if not r[1]]
    for name, ok, detail in results:
        print("  [%s] %s" % ("PASS" if ok else "FAIL", name))
    if fail:
        print("\n存在失败项，请根据上面的 FAIL 详情排查后再试。")
        return 1
    print("\n全部通过！同花顺方案A已具备接入执行端的条件。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
