# -*- coding: utf-8 -*-
"""本机自测：「行情主程序 + F12 拉起下单」启动模式（EXECUTOR_THS_LAUNCH_MODE=main_f12）。

为什么需要它（2026-09-13 用户实测）：单独启动 xiadan.exe 会被同花顺前端风控
（下单时"证券代码查不出名称和价格"，之后所有单都卡住）；正确做法是先启动行情
主程序 hexin.exe，再按 F12 拉起交易模块 —— 复用已登录会话与行情推送通道，不触发风控。

用法（项目根或任意目录都能跑）：
    venv\\Scripts\\python.exe tools\\test_ths_main_f12.py            # 只体检、不启动（默认，安全）
    venv\\Scripts\\python.exe tools\\test_ths_main_f12.py --launch   # 真去拉起行情并发送 F12

--launch 只做"打开交易界面"这一件事：起行情主程序 -> 等主窗口 -> 置前 -> F12 ->
轮询等「网上股票交易系统5.0」出现。**不下单、不碰交易表单**。
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
EXEC_DIR = os.path.join(ROOT, "executor")
sys.path.insert(0, EXEC_DIR)


def hr():
    print("-" * 62)


print("=" * 62)
print("RelayGo · 执行端「行情主程序 + F12」启动模式 · 本机自测")
print("=" * 62)

# ---------- [1] 配置读取 ----------
try:
    import config
except Exception as e:
    print("[FAIL] 导入 executor/config.py 失败: %s" % e)
    sys.exit(1)

print("\n[1] 配置读取")
print("  下单程序 xiadan = %s" % config.THS_XIADAN_PATH)
print("  行情主程序 main = %s" % config.THS_MAIN_PATH)
print("  启动模式 mode   = %s" % config.THS_LAUNCH_MODE)
print("  等主窗口(秒)     = %s（含「就绪门槛」：面积/响应/尺寸稳定）"
      % config.THS_MAIN_WAIT)
print("  就绪门槛         = 面积≥%s px²，连续稳定 %s 次采样"
      % (config.THS_MAIN_MIN_AREA, config.THS_MAIN_READY_STABLE))
print("  F12 重试/单次/总 = %s 次 / %s 秒 / %s 秒"
      % (config.THS_F12_RETRY, config.THS_F12_PER_WAIT, config.THS_F12_TOTAL_WAIT))
print("  深度恢复等退出   = %s 秒" % config.THS_MAIN_EXIT_WAIT)
for label, p in (("xiadan.exe", config.THS_XIADAN_PATH),
                 ("hexin.exe", config.THS_MAIN_PATH)):
    print("  存在性 %-12s %s" % (label, "存在" if os.path.isfile(p) else "**不存在**"))
if config.THS_LAUNCH_MODE == "main_f12":
    print("  -> 当前为「行情 + F12」模式（避风控，推荐）")
else:
    print("  -> 当前为 standalone 模式（直接启动 xiadan.exe，会随机被风控）")

# ---------- [2] 窗口 / 进程体检 ----------
print("\n[2] 窗口 / 进程体检")
broker_ths = None
try:
    import broker_ths
    import ths_watchdog
    from foreground import (THS_TITLE_KEYWORD, find_window_by_title,
                            main_window_status)
except Exception as e:
    print("[WARN] 导入 broker_ths/foreground 失败（pywin32/easytrader 环境问题）: %s" % e)

if broker_ths is not None:
    try:
        main_wins = (broker_ths._windows_of_path(config.THS_MAIN_PATH)
                     if os.path.isfile(config.THS_MAIN_PATH) else [])
        print("  行情主窗口(按 exe 路径找) = %s" % (main_wins or "未找到"))
    except Exception as e:
        print("  行情主窗口探测失败: %s" % e)
    try:
        tw = find_window_by_title(THS_TITLE_KEYWORD)
        print("  交易窗口(标题含'%s') = %s" % (THS_TITLE_KEYWORD, tw or "未找到"))
        if tw:
            print("  交易窗口状态 = %s" % main_window_status(tw))
    except Exception as e:
        print("  交易窗口探测失败: %s" % e)
    try:
        print("  hexin  进程 PID = %s" % (broker_ths._pids_of_xiadan(config.THS_MAIN_PATH) or "无"))
        print("  xiadan 进程 PID = %s" % (broker_ths._pids_of_xiadan(config.THS_XIADAN_PATH) or "无"))
    except Exception as e:
        print("  进程探测失败: %s" % e)

# ---------- [3] 真拉起（可选） ----------
if "--launch" in sys.argv:
    print("\n[3] 真拉起：行情主程序 -> 等主窗口就绪 -> F12 -> 交易窗口")
    if broker_ths is None:
        print("[SKIP] broker_ths 不可用，无法真拉起")
    else:
        b = broker_ths.ThsBroker(config.THS_XIADAN_PATH)
        t0 = time.time()
        try:
            # 先单独跑一次就绪门槛，看清"行情主窗口到底多久才真可用"：
            # 这条是新加的加固 —— 以前是"窗口一出现就发 F12"，[1] 那秒常常
            # 句柄还在变（闪屏/登录框），F12 就丢了。
            tr = time.time()
            hwnd_ready = broker_ths._wait_main_window(
                config.THS_MAIN_PATH, max(10, config.THS_MAIN_WAIT))
            print("  就绪门槛：%s（耗时 %.1f 秒）"
                  % ("通过 hwnd=%s" % hwnd_ready if hwnd_ready
                     else "**超时未就绪**（仍会退化为尽力发 F12）", time.time() - tr))
            if hwnd_ready:
                print("  该窗口响应探测 = %s，面积 = %s"
                      % (broker_ths._window_responds(hwnd_ready),
                         broker_ths._window_area(hwnd_ready)))
        except Exception as e:
            print("  就绪门槛探测异常: %s" % e)
        try:
            ok = b._launch_main()
        except Exception as e:
            ok = False
            print("  _launch_main() 抛异常: %s" % e)
        print("  _launch_main() = %s（总耗时 %.1f 秒）" % (ok, time.time() - t0))
        tw = find_window_by_title(THS_TITLE_KEYWORD)
        print("  交易窗口 = %s" % (tw or "未找到"))
        if tw:
            print("  交易窗口状态 = %s" % main_window_status(tw))
        try:
            wd = ths_watchdog.watchdog
            print("  看门狗：连续 %s 次 / 窗口内 %s 次（最近异常=%s）"
                  % (wd.consecutive(), wd.error_count(), wd.last_kind or "-"))
        except Exception:
            pass
        hr()
        print("  结论: %s" % ("F12 已成功拉起交易模块 —— 该模式可用"
                              if ok else
                              "**未拉起，请检查行情主程序路径 / 行情是否已登录 / "
                              "窗口是否无响应**"))
else:
    print("\n[3] 未加 --launch，跳过真实拉起（仅体检，不改动本机界面）")
    print("    要真测: venv\\Scripts\\python.exe tools\\test_ths_main_f12.py --launch")

hr()
print("完成。")
