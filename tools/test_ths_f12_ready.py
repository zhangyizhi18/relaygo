# -*- coding: utf-8 -*-
"""离线回归：F12 拉起交易模块的「就绪门槛 + 重试 + 看门狗上报」（2026-09-13）。

对应现场问题（用户反馈）：
  "执行端在行情程序还没完全启动就发送 F12，有时候拉起下单程序失败，
   而看门狗也没起作用进行错误处理。"

本测试**不联网、不启动同花顺、不碰 UI**：全部用桩替换 win32 窗口 API 与
_send_f12/_pick_main_window 等，只验证 broker_ths 的决策逻辑。

覆盖点：
  [1] _window_usable      可见 / 未最小化 / 面积够大 / 消息循环有响应 —— 缺一不可
  [2] _wait_main_window   闪屏(小)不算就绪；尺寸稳定后才算；句柄乱变不算
  [3] _send_f12           置前后复核前台窗口：前台上不是目标窗口则不发按键
  [4] _launch_main        失败必上报看门狗；每轮重新定位窗口；成功则清零计数
  [5] restart_ths          main_f12 模式始终「连行情主程序+交易模块一起」完整重拉（force_restart_main）
  [6] 看门狗联动           连续 3 次启动失败 → should_restart() 为真
  [7] 主框架识别           登录框(#32770)不算「行情主窗口」
  [8] 交易窗口确认         「稳定 + 有响应」才算拉起成功（防假成功）
  [9] _relaunch_trading_module   交易窗口消失/不出 -> 重新定位行情主窗口并重发 F12
  [10] ensure_started      窗口迟迟不出 -> 期间多次重发 F12（不再干等满超时）

用法：venv\\Scripts\\python.exe tools\\test_ths_f12_ready.py
"""
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
EXEC_DIR = os.path.join(ROOT, "executor")
sys.path.insert(0, EXEC_DIR)

import broker_ths                      # noqa: E402
import ths_watchdog                    # noqa: E402

# 先留住"真实现"的引用：第 [4] 节的 launch_setup 会把 _wait_main_window /
# _pick_main_window / _windows_of_path 等都换成桩且不还原（那些桩是给 _launch_main
# 用的），后面再用 wait_case 测"就绪判定"时必须用真实现，否则测到的是桩。
_REAL_WAIT_MAIN_WINDOW = broker_ths._wait_main_window
_REAL_PICK_MAIN_WINDOW = broker_ths._pick_main_window

PASS = [0]
FAIL = [0]


def check(name, cond, extra=""):
    if cond:
        PASS[0] += 1
        print("  [OK]   %s" % name)
    else:
        FAIL[0] += 1
        print("  [FAIL] %s %s" % (name, extra))


def hr(title=""):
    print("-" * 66)
    if title:
        print(title)


# ---------------- 桩 ----------------

class FakeWin32Gui(object):
    """只实现 broker_ths 用到的那几个接口。"""

    def __init__(self):
        self.rect = {}        # hwnd -> (l, t, r, b)
        self.visible = {}
        self.iconic = {}
        self.fg = 0           # 前台窗口
        self.roots = {}       # hwnd -> root owner
        self.cls = {}         # hwnd -> 类名
        self.title = {}       # hwnd -> 标题

    # 配置辅助
    def add(self, hwnd, w, h, visible=True, iconic=False, cls="", title=""):
        self.rect[hwnd] = (0, 0, w, h)
        self.visible[hwnd] = visible
        self.iconic[hwnd] = iconic
        self.cls[hwnd] = cls
        self.title[hwnd] = title
        return hwnd

    # win32gui 接口
    def IsWindow(self, h):
        return h in self.rect

    def IsWindowVisible(self, h):
        return bool(self.visible.get(h, False))

    def IsIconic(self, h):
        return bool(self.iconic.get(h, False))

    def GetWindowRect(self, h):
        return self.rect[h]

    def GetForegroundWindow(self):
        return self.fg

    def GetAncestor(self, h, flag):
        return self.roots.get(h, h)

    def GetClassName(self, h):
        return self.cls.get(h, "")

    def GetWindowText(self, h):
        return self.title.get(h, "")


class FakeTime(object):
    """受控时钟：sleep(n) 就把时间推进 n 秒，使重试循环瞬间跑完。"""

    def __init__(self, step=0.6):
        self.t = 1000.0
        self.step = step

    def time(self):
        self.t += self.step
        return self.t

    def sleep(self, s=0.0):
        self.t += max(0.0, float(s))


def install_fake_gui():
    gui = FakeWin32Gui()
    sys.modules["win32gui"] = gui          # 让函数内的 `import win32gui` 拿到桩
    return gui


def make_broker(ui_log):
    """造一个 ThsBroker：只替换 _ui_error 做记录，其余为真实现。"""
    b = broker_ths.ThsBroker(r"D:\fake\xiadan.exe")
    orig = b._ui_error

    def _rec(kind, detail=""):
        ui_log.append((kind, detail))
        return orig(kind, detail)

    b._ui_error = _rec
    return b


def fresh_watchdog(consecutive=3, threshold=5):
    wd = ths_watchdog.UiWatchdog(window=60, consecutive=consecutive,
                                 threshold=threshold, cooldown=0,
                                 max_per_hour=3, enabled=True)
    ths_watchdog.watchdog = wd
    return wd


print("=" * 66)
print("RelayGo · F12 拉起交易模块：就绪门槛 / 重试 / 看门狗上报 · 离线回归")
print("=" * 66)

# ============================================================
hr("\n[1] _window_usable：可见 + 未最小化 + 面积达标 + 消息循环有响应")

gui = install_fake_gui()
big = gui.add(101, 1280, 800)         # 正常主窗口
small = gui.add(102, 320, 180)        # 闪屏/小提示窗
mini = gui.add(103, 1280, 800, iconic=True)
hidden = gui.add(104, 1280, 800, visible=False)

broker_ths._window_responds = lambda hwnd, timeout_ms=800: True
check("可见 + 面积达标(1280x800) -> 可用",
      broker_ths._window_usable(big, 120000) is True)
check("面积不足(320x180=57600 < 120000) -> 不可用（闪屏不算）",
      broker_ths._window_usable(small, 120000) is False)
check("最小化 -> 不可用（最小化收不到 F12）",
      broker_ths._window_usable(mini, 120000) is False)
check("不可见 -> 不可用",
      broker_ths._window_usable(hidden, 120000) is False)
broker_ths._window_responds = lambda hwnd, timeout_ms=800: False
check("消息循环无响应(半启动) -> 不可用",
      broker_ths._window_usable(big, 120000) is False)
broker_ths._window_responds = lambda hwnd, timeout_ms=800: True
check("不存在的句柄 -> 不可用", broker_ths._window_usable(99999, 0) is False)

# ============================================================
hr("\n[2] _wait_main_window：闪屏不算就绪，尺寸稳定才算")

# 这里**不替换 _pick_main_window**，而是替换更底层的 _windows_of_path：
# 让 _pick_main_window 真正跑"面积/可见/响应"过滤，这样测的就是真实链路。
_orig_wins = broker_ths._windows_of_path
_orig_responds = broker_ths._window_responds


def wait_case(seq, timeout=2.0, stable=2, min_area=120000):
    """seq：每次 _windows_of_path 返回的窗口列表（最后一个重复使用）。"""
    calls = {"i": 0}

    def wins_of_path(exe_path, visible_only=True):
        i = min(calls["i"], len(seq) - 1)
        calls["i"] += 1
        return list(seq[i])

    broker_ths._windows_of_path = wins_of_path
    _saved_pick = broker_ths._pick_main_window
    broker_ths._pick_main_window = _REAL_PICK_MAIN_WINDOW   # 第 [4] 节的桩要换回真实现
    try:
        return _REAL_WAIT_MAIN_WINDOW("D:\\fake\\hexin.exe", timeout,
                                      min_area=min_area, stable=stable)
    finally:
        broker_ths._windows_of_path = _orig_wins
        broker_ths._pick_main_window = _saved_pick


# 句柄一直很小（闪屏一直不走）→ 不算就绪
gui = install_fake_gui()
splash = gui.add(201, 320, 180)
check("闪屏一直存在(面积不足) -> 超时返回 0（不会对着闪屏发 F12）",
      wait_case([[splash]], timeout=1.2) == 0)

# 闪屏(小) -> 主窗(大) -> 尺寸稳定 -> 就绪
gui = install_fake_gui()
splash = gui.add(201, 320, 180)
main = gui.add(202, 1280, 800)
h = wait_case([[splash], [splash], [main], [main], [main], [main]], timeout=3.0)
check("闪屏->主窗 且尺寸稳定 -> 返回主窗句柄", h == main, "得到 %s" % h)

# 主窗尺寸每轮都在变（还在重画/布局未定型）→ 不算就绪
gui = install_fake_gui()
a = gui.add(301, 1280, 800)
b = gui.add(302, 1200, 700)
check("主窗尺寸一直在变 -> 超时返回 0",
      wait_case([[a], [b], [a], [b], [a], [b], [a], [b]], timeout=1.2) == 0)

# 响应探测持续失败 -> 不算就绪
gui = install_fake_gui()
big = gui.add(401, 1280, 800)
broker_ths._window_responds = lambda hwnd, timeout_ms=800: False
try:
    check("消息循环一直无响应 -> 超时返回 0",
          wait_case([[big]] * 8, timeout=1.2) == 0)
finally:
    broker_ths._window_responds = _orig_responds

# ============================================================
hr("\n[3] _send_f12：置前后复核前台窗口，不是目标窗口就不发按键")

sent = []
fake_kb = types.ModuleType("pywinauto.keyboard")
fake_kb.send_keys = lambda s: sent.append(s)
sys.modules["pywinauto.keyboard"] = fake_kb

gui = install_fake_gui()
main = gui.add(501, 1280, 800)
other = gui.add(502, 1280, 800)

# 置前会被 force_foreground 覆盖，这里直接让 force_foreground 变成"把 fg 设成目标"
_orig_ff = broker_ths.force_foreground

gui.fg = other
broker_ths.force_foreground = lambda hwnd: True      # 假装置前"成功"但前台其实没变
sent[:] = []
ok = broker_ths._send_f12(main)
check("前台不是目标窗口 -> 返回 False 且不注入按键", ok is False and sent == [],
      "ok=%s sent=%s" % (ok, sent))

gui.fg = main
sent[:] = []
ok = broker_ths._send_f12(main)
check("前台就是目标窗口 -> 注入 {F12}", ok is True and sent == ["{F12}"],
      "ok=%s sent=%s" % (ok, sent))

# 目标是被拥有的子窗口、前台是它的 root owner：也算命中
child = gui.add(503, 600, 400)
gui.roots[child] = main
gui.fg = main
sent[:] = []
check("前台是目标窗口的 root owner -> 仍然发送",
      broker_ths._send_f12(child) is True and sent == ["{F12}"])

# 拿不到前台窗口（锁屏/远程桌面断开/会话无活动桌面）-> 不能拦，退化为旧行为
gui.fg = 0
sent[:] = []
check("GetForegroundWindow()=0 -> 放行（否则锁屏时永远发不出 F12）",
      broker_ths._send_f12(main) is True and sent == ["{F12}"],
      "sent=%s" % sent)

# 探测抛异常 -> 同样放行
gui.fg = main
_orig_ff2 = broker_ths.force_foreground
broker_ths.force_foreground = lambda hwnd: False     # 置前失败，但前台恰好就是它
sent[:] = []
check("置前返回失败但前台确实是目标窗口 -> 仍然发送",
      broker_ths._send_f12(main) is True and sent == ["{F12}"])
broker_ths.force_foreground = _orig_ff2

broker_ths.force_foreground = _orig_ff

# ============================================================
hr("\n[4] _launch_main：失败必上报看门狗；每轮重新定位窗口")


def launch_setup(f12_result, hwnds, trading_after=99, pids=(4242,), ui_log=None):
    """把 _launch_main 的外部依赖全部换桩，返回 (broker, 记录容器)。"""
    rec = {"f12": [], "pick": 0, "killed": [], "started": []}
    b = make_broker(ui_log if ui_log is not None else [])
    broker_ths.config.THS_MAIN_PATH = os.path.abspath(__file__)   # 必须"存在"
    broker_ths.config.THS_LAUNCH_MODE = "main_f12"
    broker_ths._pids_of_xiadan = lambda p: list(pids)
    broker_ths._kill_by_path = lambda p: rec["killed"].append(p) or list(pids)
    b._start_process = lambda p: (rec["started"].append(p), True)[1]
    broker_ths._wait_main_window = lambda p, t, **kw: hwnds[0] if hwnds else 0

    def pick(p, area=0, **kw):
        rec["pick"] += 1
        return hwnds[min(rec["pick"] - 1, len(hwnds) - 1)] if hwnds else 0

    broker_ths._pick_main_window = pick
    broker_ths._windows_of_path = lambda p, visible_only=True: list(hwnds)
    # _launch_main 现在用 _confirm_trading_window 判断"交易窗口真的起来了"，
    # 它会再问一次消息循环是否有响应 —— 离线环境没有真窗口，必须一并桩掉，
    # 否则会去戳真实句柄、判定随机。
    broker_ths._window_responds = lambda hwnd, timeout_ms=800: True

    def send(hwnd=0):
        rec["f12"].append(hwnd)
        return f12_result(hwnd) if callable(f12_result) else bool(f12_result)

    broker_ths._send_f12 = send
    counters = {"n": 0}

    def find_title(kw):
        counters["n"] += 1
        return 777 if counters["n"] >= trading_after else 0

    broker_ths.find_window_by_title = find_title
    broker_ths.time = FakeTime()
    return b, rec


# 4.1 行情主程序路径不存在 -> launch_fail
ui = []
wd = fresh_watchdog()
b = make_broker(ui)
_saved_main = broker_ths.config.THS_MAIN_PATH
broker_ths.config.THS_MAIN_PATH = r"D:\definitely\not\here\hexin.exe"
ok = b._launch_main()
check("行情主程序路径不存在 -> 返回 False", ok is False)
check("...并上报 launch_fail", ui and ui[0][0] == "launch_fail", str(ui))
check("...看门狗连续计数 +1", wd.consecutive() == 1)
broker_ths.config.THS_MAIN_PATH = _saved_main

# 4.2 F12 发满仍无交易窗口 -> f12_launch_fail（只上报一次）
ui = []
wd = fresh_watchdog()
b, rec = launch_setup(lambda h: True, [501], trading_after=10 ** 9, ui_log=ui)
ok = b._launch_main()
check("F12 发满 6 次仍无交易窗口 -> 返回 False", ok is False, "ok=%s" % ok)
check("...发送次数 = THS_F12_RETRY(6)", len(rec["f12"]) == 6,
      "实际 %d 次" % len(rec["f12"]))
check("...只上报一次 f12_launch_fail", [k for k, _ in ui] == ["f12_launch_fail"],
      str(ui))
check("...看门狗连续计数 = 1", wd.consecutive() == 1, "得到 %s" % wd.consecutive())

# 4.3 每轮重新定位窗口：第 1 次句柄已失效(发送失败) -> 第 2 次新句柄成功
ui = []
wd = fresh_watchdog()
b, rec = launch_setup(lambda h: h == 502, [501, 502], trading_after=1, ui_log=ui)
ok = b._launch_main()
check("第 1 次发给失效句柄(失败)后，第 2 次用新句柄成功 -> 返回 True", ok is True)
check("...确实向两个不同句柄各试过一次", rec["f12"] == [501, 502], str(rec["f12"]))
check("...成功不再上报界面异常", ui == [], str(ui))
check("...成功后看门狗连续计数被清零", ths_watchdog.watchdog.consecutive() == 0)

# 4.4 成功时清零此前累计的连续界面异常
wd = fresh_watchdog()
wd.report_ui_error("not_registered", "x")
wd.report_ui_error("not_registered", "y")
ui = []
b, rec = launch_setup(lambda h: True, [501], trading_after=1, ui_log=ui)
check("启动前累计 2 次界面异常", ths_watchdog.watchdog.consecutive() == 2)
ok = b._launch_main()
check("拉起成功后连续计数归零（界面已恢复）",
      ok is True and ths_watchdog.watchdog.consecutive() == 0)

# ============================================================
hr("\n[5] 深度恢复：F12 拉不起来 -> 连行情主程序一起重启")

ui = []
wd = fresh_watchdog()
b, rec = launch_setup(lambda h: True, [501], trading_after=10 ** 9, ui_log=ui)
_hexin = broker_ths.config.THS_MAIN_PATH
ok = b._launch_main(force_restart_main=True)
check("force_restart_main=True 且行情在运行 -> 先结束行情主程序",
      rec["killed"] == [_hexin], str(rec["killed"]))
check("...随后重新启动行情主程序",
      rec["started"] == [_hexin], str(rec["started"]))
check("...仍然拉不起 -> 返回 False", ok is False)

# restart_ths：main_f12 模式始终「连行情主程序+交易模块一起」完整重拉
#   （用户要求：重启执行端时应把行情和下单软件一起重启，而不是只重拉交易模块）
calls = []
b = make_broker([])
b._launch_main = lambda force_restart_main=False: (
    calls.append(force_restart_main), False)[1]
b._launch_standalone = lambda: False      # 防止离线测试真去 os.startfile
broker_ths.config.THS_LAUNCH_MODE = "main_f12"
broker_ths._pids_of_xiadan = lambda p: []  # 视为「未运行」：跳过结束进程直接启动
r = b.restart_ths()
check("restart_ths(main_f12)：始终调用完整重拉（force_restart_main=True）",
      calls == [True], str(calls))
check("...完整重拉也失败时如实报错（提示行情主程序与交易模块完整重拉仍失败）",
      r.get("ok") is False and "完整重拉仍失败" in r.get("message", ""),
      str(r))

# main_f12 + 视为已运行：应同时结束交易模块与行情主程序两个进程
_killed = []
_orig_kill = broker_ths._kill_by_path
_orig_pids = broker_ths._pids_of_xiadan
_orig_main = broker_ths.config.THS_MAIN_PATH
broker_ths._kill_by_path = lambda p: (_killed.append(p), [9999])[1]
broker_ths._pids_of_xiadan = lambda p: [1234]
broker_ths.config.THS_MAIN_PATH = r"D:\__TEST__\hexin.exe"   # 确保非空
broker_ths.config.THS_LAUNCH_MODE = "main_f12"
b2 = make_broker([])
b2._launch_main = lambda force_restart_main=False: True
b2.wait_until_ready = lambda timeout=None: (True, "ok")
r2 = b2.restart_ths()
check("restart_ths(main_f12, 已运行)：同时结束交易模块与行情主程序",
      broker_ths.config.THS_MAIN_PATH in _killed
      and b2.xiadan_path in _killed, str(_killed))
check("...结束后调用完整重拉并连接成功", r2.get("ok") is True, str(r2))
broker_ths._kill_by_path = _orig_kill
broker_ths._pids_of_xiadan = _orig_pids
broker_ths.config.THS_MAIN_PATH = _orig_main
broker_ths.config.THS_LAUNCH_MODE = "main_f12"

broker_ths.config.THS_LAUNCH_MODE = "standalone"
calls[:] = []
r = b.restart_ths()
check("standalone 模式不调用 _launch_main（返回启动失败并给出 xiadan 路径）",
      calls == [] and r.get("ok") is False and "xiadan.exe" in r.get("message", ""),
      str(r))
broker_ths.config.THS_LAUNCH_MODE = "main_f12"

# ============================================================
hr("\n[6] 看门狗联动：连续 3 次启动失败 -> 该重启了")

wd = fresh_watchdog(consecutive=3, threshold=5)
for i in range(2):
    wd.report_ui_error("f12_launch_fail", "第 %d 次" % (i + 1))
check("连续 2 次启动失败 -> 还不重启", wd.should_restart() is False)
wd.report_ui_error("f12_launch_fail", "第 3 次")
check("连续 3 次启动失败 -> should_restart() 为真", wd.should_restart() is True)
check("...原因字符串里能看到 f12_launch_fail",
      "f12_launch_fail" in wd.reason(), wd.reason())
wd.end_restart(True)
check("重启完成后计数清零", wd.consecutive() == 0 and wd.error_count() == 0)

wd_cd = ths_watchdog.UiWatchdog(window=60, consecutive=3, threshold=5,
                                cooldown=120, max_per_hour=3, enabled=True)
wd_cd.report_ui_error("f12_launch_fail", "x")
wd_cd.end_restart(True)
check("重启后进入冷却（冷却期内不再触发）",
      wd_cd.in_cooldown() is True and wd_cd.should_restart() is False)

# 业务失败（风控拒绝）不进看门狗：只有显式 report_ui_error 才计数
wd2 = fresh_watchdog()
check("没有界面异常上报时永远不重启（业务失败不会误触发）",
      wd2.should_restart() is False and wd2.consecutive() == 0)

# ============================================================
hr("\n[7] 主框架识别：登录框(#32770)不算「行情主窗口」")
# 现场铁证（2026-09-13, tools/ths_launch_trace.py）：
#   19:27:05 F12 发给 #32770 title='登录到全部行情主站' 600x430
#            -> xiadan.exe 起来 3.5 秒就自退，60 秒后窗口消失、连接失败
#   19:28:44 等主框架 Afx:... title='同花顺(9.60.60) - 自选股' 1934x1054 再发 F12
#            -> xiadan 稳定存活，4 秒后「券商连接自检: True 连接正常」

gui = install_fake_gui()
broker_ths._window_responds = lambda hwnd, timeout_ms=800: True
dlg = gui.add(701, 600, 430, cls="#32770", title="登录到全部行情主站")
frame = gui.add(702, 1934, 1054, cls="Afx:004A0000:b:00010003:00000006:33A81AF1",
                title="同花顺(9.60.60) - 自选股")
chrome = gui.add(703, 1440, 900, cls="Chrome_WidgetWin_1", title="同花顺")

check("登录对话框(#32770 '登录到全部行情主站') -> 不当作行情主窗口",
      broker_ths._looks_like_main_frame(dlg) is False)
check("MFC 主框架(Afx: + 标题带同花顺) -> 当作行情主窗口",
      broker_ths._looks_like_main_frame(frame) is True)
check("新版 Chromium 主框架(Chrome_WidgetWin_1) -> 当作行情主窗口",
      broker_ths._looks_like_main_frame(chrome) is True)
check("_window_usable(main_frame_only=True) 会排除登录框",
      broker_ths._window_usable(dlg, 120000, main_frame_only=True) is False)
check("_window_usable(main_frame_only=True) 仍认可主框架",
      broker_ths._window_usable(frame, 120000, main_frame_only=True) is True)

# 登录框先出现（可见/够大/稳定/有响应 —— 旧判据会认它）→ 新判据必须继续等主框架
gui = install_fake_gui()
broker_ths._window_responds = lambda hwnd, timeout_ms=800: True
dlg = gui.add(711, 600, 430, cls="#32770", title="登录到全部行情主站")
frame = gui.add(712, 1934, 1054, cls="Afx:004A0000:b", title="同花顺(9.60.60) - 自选股")
h = wait_case([[dlg], [dlg], [dlg], [frame], [frame], [frame], [frame], [frame]],
              timeout=12.0)     # 第[4]节把 time 换成了 FakeTime（每次采样推进 ~1.1s）
check("登录框先来 -> 不返回它，等到主框架才算就绪", h == frame, "得到 %s" % h)

# ============================================================
hr("\n[8] 交易窗口必须「稳定 + 有响应」才算拉起成功（防假成功）")

_orig_find2 = broker_ths.find_window_by_title
_resp2 = broker_ths._window_responds

# 窗口在，但消息循环无响应 = 正处于"被拉起后自退"的状态 -> 不算成功
broker_ths._window_responds = lambda hwnd, timeout_ms=700: False
broker_ths.find_window_by_title = lambda kw: 801
h, exe = broker_ths._confirm_trading_window(timeout=1.2, stable=1, interval=0.2)
check("窗口存在但无响应 -> 确认失败（不把'起来就自退'当成功）", h == 0, "得到 %s" % h)

# 稳定 + 有响应 -> 确认成功，并带出归属 EXE
broker_ths._window_responds = lambda hwnd, timeout_ms=700: True
h, exe = broker_ths._confirm_trading_window(timeout=2.0, stable=2, interval=0.2)
check("窗口稳定且响应 -> 确认成功（返回该句柄）", h == 801, "得到 %s" % h)

# 只闪现一次就消失 -> 稳定不了 -> 不算成功（旧实现会在这里谎报成功）
seq = [901, 0, 0, 0]
it = [0]


def _flaky(kw):
    i = min(it[0], len(seq) - 1)
    it[0] += 1
    return seq[i]


broker_ths.find_window_by_title = _flaky
h, exe = broker_ths._confirm_trading_window(timeout=1.5, stable=2, interval=0.2)
check("窗口闪现一次就消失 -> 确认失败（旧实现在这里会谎报成功）", h == 0,
      "得到 %s" % h)

# 句柄每轮都换（被反复重建）-> 稳定不了 -> 不算成功
seq2 = [902, 903, 904, 905, 906]
it2 = [0]


def _churn(kw):
    i = min(it2[0], len(seq2) - 1)
    it2[0] += 1
    return seq2[i]


broker_ths.find_window_by_title = _churn
h, exe = broker_ths._confirm_trading_window(timeout=1.5, stable=2, interval=0.2)
check("交易窗口句柄一直在变 -> 确认失败", h == 0, "得到 %s" % h)

broker_ths.find_window_by_title = _orig_find2
broker_ths._window_responds = _resp2

# ============================================================
hr("\n[9] _relaunch_trading_module：重新定位行情主窗口并重发 F12")
# 现场问题（2026-09-13）：_launch_main 用"稳定 1 秒"判成功，交易窗口起来 3 秒
# 又自退时它会**误报成功**并返回 True；随后 ensure_started 的等待循环干等满
# 60 秒、期间从不重发 F12 —— 用户看到的就是"能启动行情，却一直拉不起下单程序"。

_save_pick = broker_ths._pick_main_window
_save_send = broker_ths._send_f12
_save_confirm = broker_ths._confirm_trading_window
_save_main = broker_ths.config.THS_MAIN_PATH
broker_ths.config.THS_MAIN_PATH = os.path.abspath(__file__)   # 必须"存在"
b9 = make_broker([])

# 9.1 连行情主窗口都找不到 -> (False, 0)
broker_ths._pick_main_window = lambda p, area=0, **kw: 0
ok, h = b9._relaunch_trading_module()
check("找不到行情主窗口 -> (False, 0)", ok is False and h == 0)

# 9.2 找到行情主窗口但 F12 发不出去 -> (False, 0)
picks = []


def _pick_spy(p, area=0, **kw):
    picks.append(kw.get("main_frame_only"))
    return 601


broker_ths._pick_main_window = _pick_spy
broker_ths._send_f12 = lambda hwnd=0: False
ok, h = b9._relaunch_trading_module()
check("行情主窗口在但 F12 发送失败 -> (False, 0)", ok is False and h == 0)
check("...定位时要求「只认主框架」(main_frame_only=True)",
      picks and picks[0] is True, str(picks))

# 9.3 F12 发出且交易窗口确认稳定 -> (True, hwnd)
sent9 = []
broker_ths._send_f12 = lambda hwnd=0: (sent9.append(hwnd), True)[1]
broker_ths._confirm_trading_window = lambda *a, **kw: (888, r"D:\fake\xiadan.exe")
ok, h = b9._relaunch_trading_module()
check("F12 发出 + 交易窗口确认稳定 -> (True, 888)", ok is True and h == 888)
check("...F12 确实发给了重新定位到的行情主窗口", sent9 == [601], str(sent9))

# 9.4 F12 发出但交易窗口始终没确认 -> (False, 0)
broker_ths._confirm_trading_window = lambda *a, **kw: (0, "")
ok, h = b9._relaunch_trading_module()
check("F12 发出但交易窗口未确认 -> (False, 0)", ok is False and h == 0)

broker_ths._pick_main_window = _save_pick
broker_ths._send_f12 = _save_send
broker_ths._confirm_trading_window = _save_confirm
broker_ths.config.THS_MAIN_PATH = _save_main

# ============================================================
hr("\n[10] ensure_started：交易窗口迟迟不出 -> 期间会重发 F12（不再干等满超时）")

_save_time9 = broker_ths.time
_save_find9 = broker_ths.find_window_by_title
_save_ready9 = broker_ths.main_window_ready
_save_status9 = broker_ths.main_window_status
_save_ff9 = broker_ths.force_foreground
_save_proc9 = broker_ths._process_running_by_path
_save_auto9 = broker_ths.config.THS_AUTOSTART
_save_to9 = broker_ths.config.THS_AUTOSTART_TIMEOUT
_save_gap9 = getattr(broker_ths.config, "THS_RESEND_F12_GAP", None)

broker_ths.config.THS_AUTOSTART = True
broker_ths.config.THS_AUTOSTART_TIMEOUT = 40
broker_ths.config.THS_RESEND_F12_GAP = 8
broker_ths._process_running_by_path = lambda p: False
broker_ths.force_foreground = lambda h: True
broker_ths.main_window_ready = lambda h: True
broker_ths.main_window_status = lambda h: "交易窗口就绪"
broker_ths.time = FakeTime(step=0.5)

# 10a 交易窗口始终不出现 -> 期间应多次重发 F12，最终才返回 False
b10 = broker_ths.ThsBroker(r"D:\fake\xiadan.exe")
b10.dismiss_blocking_dialogs = lambda **kw: 0
b10.assist_login = lambda **kw: {}
b10._launch = lambda: True
broker_ths.find_window_by_title = lambda kw: 0
resends = []
b10._relaunch_trading_module = lambda: (resends.append(1), (False, 0))[1]
try:
    ok10 = b10.ensure_started()
finally:
    pass
check("窗口始终不出现 -> 最终返回 False", ok10 is False)
check("...期间多次重发 F12（旧实现只会干等满超时）",
      len(resends) >= 2, "重发 %d 次" % len(resends))

# 10b 交易窗口很快出现且就绪 -> 立即收工，不重发
broker_ths.time = FakeTime(step=0.5)
b10b = broker_ths.ThsBroker(r"D:\fake\xiadan.exe")
b10b.dismiss_blocking_dialogs = lambda **kw: 0
b10b.assist_login = lambda **kw: {}
b10b._launch = lambda: True
resends2 = []
b10b._relaunch_trading_module = lambda: (resends2.append(1), (False, 0))[1]
seq10b = {"n": 0}


def _find_ready(k):
    seq10b["n"] += 1
    return 0 if seq10b["n"] <= 1 else 999      # 第 1 次是"是否已在运行"探测


broker_ths.find_window_by_title = _find_ready
try:
    ok10b = b10b.ensure_started()
finally:
    pass
check("交易窗口很快就绪 -> True", ok10b is True)
check("...就绪后不再重发 F12（resend 为空）", resends2 == [], str(resends2))
check("...就绪窗口句柄已记录", b10b._hwnd == 999, str(b10b._hwnd))

# 10c 窗口出现但未就绪（仍在初始化）-> 不重发 F12（别打扰它登录）
broker_ths.time = FakeTime(step=0.5)
b10c = broker_ths.ThsBroker(r"D:\fake\xiadan.exe")
b10c.dismiss_blocking_dialogs = lambda **kw: 0
b10c.assist_login = lambda **kw: {}
b10c._launch = lambda: True
resends3 = []
b10c._relaunch_trading_module = lambda: (resends3.append(1), (False, 0))[1]
seq10c = {"n": 0}


def _find_slow(k):
    seq10c["n"] += 1
    return 0 if seq10c["n"] <= 1 else 555


broker_ths.find_window_by_title = _find_slow
broker_ths.main_window_ready = lambda h: False
broker_ths.main_window_status = lambda h: "交易窗口仍在初始化（工具栏/菜单树未出现）"
try:
    ok10c = b10c.ensure_started()
finally:
    pass
check("窗口在但未就绪 -> True（进程确实起来了）", ok10c is True)
check("...且不重发 F12（避免打断初始化）", resends3 == [], str(resends3))

# 10d 窗口先出现、随后又消失（典型"F12 发早了，交易模块被拉起随即自退"）
#     -> 必须**立即**重发 F12，而不是干等满 THS_RESEND_F12_GAP（用户 2026-09-13 反馈）
broker_ths.time = FakeTime(step=0.5)
b10d = broker_ths.ThsBroker(r"D:\fake\xiadan.exe")
b10d.dismiss_blocking_dialogs = lambda **kw: 0
b10d.assist_login = lambda **kw: {}
b10d._launch = lambda: True
b10d._park_main_window = lambda: None          # 离线环境无 win32，跳过真实缩窗
resends4 = []
b10d._relaunch_trading_module = lambda: (resends4.append(1), (True, 777))[1]
seq10d = {"n": 0}


def _find_appeared_then_gone(k):
    seq10d["n"] += 1
    if seq10d["n"] == 1:
        return 0        # 开头"是否已运行"探测 -> 继续走自动拉起
    if seq10d["n"] == 2:
        return 777      # 交易窗口出现（但未就绪）
    return 0            # 之后消失 -> 应触发立即重发


broker_ths.find_window_by_title = _find_appeared_then_gone
broker_ths.main_window_ready = lambda h: False   # 出现时未就绪 -> 只记 window_seen
broker_ths.main_window_status = lambda h: "交易窗口仍在初始化"
try:
    ok10d = b10d.ensure_started()
finally:
    pass
check("窗口出现后消失 -> 立即重发并成功拉起（True）", ok10d is True)
check("...且只重发 1 次（无需等满 resend_gap）", resends4 == [1], str(resends4))

# 还原现场
broker_ths.time = _save_time9
broker_ths.find_window_by_title = _save_find9
broker_ths.main_window_ready = _save_ready9
broker_ths.main_window_status = _save_status9
broker_ths.force_foreground = _save_ff9
broker_ths._process_running_by_path = _save_proc9
broker_ths.config.THS_AUTOSTART = _save_auto9
broker_ths.config.THS_AUTOSTART_TIMEOUT = _save_to9
if _save_gap9 is None:
    try:
        del broker_ths.config.THS_RESEND_F12_GAP
    except Exception:
        pass
else:
    broker_ths.config.THS_RESEND_F12_GAP = _save_gap9

hr()
print("结果：通过 %d / 失败 %d" % (PASS[0], FAIL[0]))
if FAIL[0]:
    print("**有失败项，请检查**")
sys.exit(1 if FAIL[0] else 0)
