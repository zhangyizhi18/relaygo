# -*- coding: utf-8 -*-
"""
聚宽模拟器 GUI 异常输入测试（脚本化驱动 tkinter App，不弹真窗）。
运行: 系统 Python3.9 (带 tkinter) python joinquant/test_sim_gui_ui.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jq_sim_gui as m  # noqa: E402

PASS = 0
FAIL = 0
DIALOGS = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("OK   %s" % name)
    else:
        FAIL += 1
        print("FAIL %s  %s" % (name, detail))


def fake_dialog(kind):
    def _f(*a, **k):
        DIALOGS.append((kind, str(a[1]) if len(a) > 1 else ""))
    return _f


def main():
    import tkinter as tk
    # 拦截所有弹窗（记录而非阻塞）
    m.messagebox.showwarning = fake_dialog("warn")
    m.messagebox.showinfo = fake_dialog("info")
    m.messagebox.showerror = fake_dialog("error")

    root = tk.Tk()
    root.withdraw()                      # 不显示窗口
    app = m.App(root)

    # ---- 1. 空代码下单 ----
    DIALOGS.clear()
    app.var_code.set("")
    app.var_amt.set("100")
    app.var_price.set("")
    app.do_order()
    check("空代码下单不崩溃", True)

    # ---- 2. 委托价非数字 ----
    DIALOGS.clear()
    app.var_code.set("600519.XSHG")
    app.var_amt.set("100")
    app.var_price.set("3.9.2")
    app.do_order()
    check("委托价非数字弹出警告", DIALOGS and DIALOGS[0][0] == "warn", DIALOGS)

    # ---- 3. 数量非数字 / 空 ----
    DIALOGS.clear()
    app.var_price.set("")
    app.var_amt.set("abc")
    app.do_order()
    check("数量非数字弹出警告", DIALOGS and DIALOGS[0][0] == "warn", DIALOGS)
    DIALOGS.clear()
    app.var_amt.set("")
    app.do_order()
    check("数量为空弹出警告", DIALOGS and DIALOGS[0][0] == "warn", DIALOGS)

    # ---- 4. order 传小数（如 500.5）----
    app.var_amt.set("500.5")
    app.do_order()                       # int(500.5)=500，静默取整不崩溃
    check("order 传小数不崩溃", True)

    # ---- 5. order_value 传 0 / 负 ----
    DIALOGS.clear()
    app.var_func.set("order_value")
    app.var_amt.set("0")
    app.do_order()
    check("order_value 传 0 -> None 日志", "None" in app.txt.get("1.0", "end"))
    app.var_amt.set("-5000")
    app.do_order()                       # 卖出 5000 元 -> 无持仓 None
    check("order_value 负数无持仓不崩溃", True)

    # ---- 6. 添加标的：负价 / 0 价 / 非数字 ----
    DIALOGS.clear()
    app.var_ncode.set("000001.XSHE")
    app.var_nname.set("平安银行")
    app.var_nprice.set("-5")
    app.add_security()
    check("负价添加被拦", DIALOGS and DIALOGS[0][0] == "warn", DIALOGS)
    DIALOGS.clear()
    app.var_nprice.set("0")
    app.add_security()
    check("0价添加被拦", DIALOGS and DIALOGS[0][0] == "warn", DIALOGS)
    DIALOGS.clear()
    app.var_nprice.set("abc")
    app.add_security()
    check("非数字价添加被拦", DIALOGS and DIALOGS[0][0] == "warn", DIALOGS)
    app.var_nprice.set("11.35")
    app.add_security()
    check("正常添加成功", "000001.XSHE" in app.eng.securities)

    # ---- 7. 改价：未选中 / 非数字 / 负数 ----
    DIALOGS.clear()
    app.tv_sec.selection_remove(app.tv_sec.selection())
    app.chg_price()
    check("未选中改价提示", DIALOGS and DIALOGS[0][0] == "info", DIALOGS)
    sel = app.tv_sec.get_children()
    app.tv_sec.selection_set(sel[0])
    DIALOGS.clear()
    app.var_newprice.set("abc")
    app.chg_price()
    check("改价非数字被拦", DIALOGS and DIALOGS[0][0] == "warn", DIALOGS)
    DIALOGS.clear()
    app.var_newprice.set("-1")
    app.chg_price()
    check("改价负数被拦", DIALOGS and DIALOGS[0][0] == "warn", DIALOGS)

    # ---- 8. 挂单操作：未选中提示 ----
    DIALOGS.clear()
    app._pend_act(True)
    app._pend_act(False)
    check("挂单未选中提示两次", len([d for d in DIALOGS if d[0] == "info"]) == 2, DIALOGS)

    # ---- 9. 重置资金：非数字 / 正常 ----
    DIALOGS.clear()
    app.var_cash.set("abc")
    app.reset()
    check("重置非数字被拦", DIALOGS and DIALOGS[0][0] == "warn", DIALOGS)
    app.var_cash.set("500000")
    app.reset()
    check("重置后资金正确且行情保留",
          abs(app.eng.cash - 500000) < 1e-6 and "600519.XSHG" in app.eng.securities,
          (app.eng.cash, list(app.eng.securities)))

    # ---- 10. 真实上报：DRY 关闭打到测试中转(5099)，验证信号行插入 ----
    app.var_url.set("http://127.0.0.1:5099")
    app.var_key.set("jq-signal-key-2026-change-me")
    app.var_dry.set(False)
    app.var_func.set("order")
    app.var_code.set("600519.XSHG")
    app.var_amt.set("100")
    app.var_price.set("")
    app.do_order()
    check("GUI 真实上报产生信号行", app.tv_sig.get_children(), "信号日志为空")
    first = app.tv_sig.item(app.tv_sig.get_children()[0])
    check("上报结果为已上报 200", "已上报" == first["values"][3] and "200" in first["values"][4],
          first["values"])

    # ---- 11. 挂单 -> 撮合/撤单按钮路径（通过引擎+GUI刷新） ----
    app.var_price.set("1600")
    app.do_order()                        # 限价 1600 挂单（远高于市价，保持 open）
    pend = app.eng.pending_orders()
    check("GUI 下限价单产生挂单", len(pend) == 1, pend)
    app.tv_pend.selection_set(app.tv_pend.get_children()[0])
    app._pend_act(False)                  # 撤单
    check("GUI 撤单后挂单清空", len(app.eng.pending_orders()) == 0)

    # ---- 12. 连接测试按钮 ----
    app.test_conn()
    check("连接测试输出成功", "连接成功" in app.txt.get("1.0", "end"))

    root.destroy()
    print("\n===== GUI 异常输入测试: %d 过 / %d 挂 =====" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
