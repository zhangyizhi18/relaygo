# -*- coding: utf-8 -*-
"""
不依赖真机的逻辑测试：验证执行端启动时"检查同花顺是否已运行、未运行则自动拉起"。

覆盖分支：
  - _process_running_by_path：win32 枚举命中 / 无命中 / 检测异常保守视为已运行
  - ensure_started：开关关闭直接返回 / 窗口已在(含托盘)不启动 / 进程在窗口不在不启动
                    / 都未运行 -> 自动拉起并轮询等到窗口出现 / 超时未出现返回 False
"""
import os
import sys
import time

sys.path.insert(0, r"<项目根目录>\executor")
import broker_ths as m
from unittest import mock

XP = r"D:\同花顺软件\同花顺\xiadan.exe"

# 本文件验证的是 **standalone** 自动拉起路径（未运行 -> os.startfile 拉起 xiadan.exe）。
# 显式固定为 standalone：默认 main_f12 会去启动行情主程序并发 F12，在装了同花顺的
# 机器上就会真的动到用户的客户端（离线测试绝不允许）。见 tools/test_ths_main_f12.py。
m.config.THS_LAUNCH_MODE = "standalone"
passed = []


def check(name, cond):
    passed.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name)


# ---------------- T1: _process_running_by_path ----------------
# 注意（2026-09-12 修正）：实现已从 psutil/wmic 改为 win32 枚举
# （_pids_of_xiadan），旧测试去 patch psutil/wmic 已无意义——在真机上
# 会因同花顺确实在运行而"意外通过/意外失败"。这里改为 patch 真正的底层
# _pids_of_xiadan，测的才是被测函数自己的逻辑。

# T1a: win32 枚举命中该路径的进程
with mock.patch.object(m, "_pids_of_xiadan", return_value=[1234]):
    check("T1a 枚举命中路径->True", m._process_running_by_path(XP) is True)

# T1b: 同路径进程不存在
with mock.patch.object(m, "_pids_of_xiadan", return_value=[]):
    check("T1b 无命中->False", m._process_running_by_path(XP) is False)

# T1c: 检测异常时不误判"没在运行"（由 ensure_started 保守处理，见 T2f）
with mock.patch.object(m, "_process_running_by_path",
                       side_effect=RuntimeError("win32 被拦截")):
    b1c = m.ThsBroker(XP)
    with mock.patch.object(m, "find_window_by_title", return_value=0), \
         mock.patch.object(m.config, "THS_AUTOSTART", True), \
         mock.patch("os.startfile") as fake_start:
        r1c = b1c.ensure_started()
        check("T1c 进程检测异常->保守视为已运行、ReturnTrue", r1c is True)
        check("T1c 进程检测异常->绝不重复拉起", fake_start.called is False)


# ---------------- T2: ensure_started 分支 ----------------
def make_broker():
    return m.ThsBroker(XP)


# T2a: 开关关闭，直接返回 True，不触碰任何启动逻辑
b = make_broker()
with mock.patch.object(m, "find_window_by_title", return_value=0), \
     mock.patch.object(m, "_process_running_by_path", return_value=False), \
     mock.patch.object(m, "force_foreground", return_value=True), \
     mock.patch.object(m.config, "THS_AUTOSTART", False), \
     mock.patch.object(b, "xiadan_path", XP):
    called = {"start": False}
    real_startfile = os.startfile
    os.startfile = lambda p: called.__setitem__("start", True)
    try:
        r = b.ensure_started()
    finally:
        os.startfile = real_startfile
    check("T2a 开关关闭->True 且不启动", r is True and not called["start"])

# T2b: 窗口已在（含托盘隐藏），不重复启动
b = make_broker()
with mock.patch.object(m, "find_window_by_title", return_value=999), \
     mock.patch.object(m, "force_foreground", return_value=True), \
     mock.patch.object(m.config, "THS_AUTOSTART", True):
    called = {"start": False}
    real_startfile = os.startfile
    os.startfile = lambda p: called.__setitem__("start", True)
    try:
        r = b.ensure_started()
    finally:
        os.startfile = real_startfile
    check("T2b 窗口已在->True 且不启动", r is True and not called["start"])

# T2c: 窗口不在但进程在，不启动
b = make_broker()
with mock.patch.object(m, "find_window_by_title", return_value=0), \
     mock.patch.object(m, "_process_running_by_path", return_value=True), \
     mock.patch.object(m, "force_foreground", return_value=True), \
     mock.patch.object(m.config, "THS_AUTOSTART", True):
    called = {"start": False}
    real_startfile = os.startfile
    os.startfile = lambda p: called.__setitem__("start", True)
    try:
        r = b.ensure_started()
    finally:
        os.startfile = real_startfile
    check("T2c 进程在窗口不在->True 且不启动", r is True and not called["start"])

# T2d: 都未运行 -> 自动拉起，轮询等到窗口出现，force_foreground 被调
# 注（2026-09-13 修）：ensure_started 的等待循环已加"真就绪"门槛
# （除了标题存在还要 main_window_ready），本用例必须把 main_window_ready
# 一并 mock，否则 hwnd=100 永远不就绪、循环把 side_effect 取空 -> StopIteration。
b = make_broker()
with mock.patch.object(m, "find_window_by_title", side_effect=[0, 100, 100]), \
     mock.patch.object(m, "main_window_ready", return_value=True), \
     mock.patch.object(m, "_process_running_by_path", return_value=False), \
     mock.patch.object(m, "force_foreground", return_value=True) as fg, \
     mock.patch.object(m.config, "THS_AUTOSTART", True), \
     mock.patch.object(m.config, "THS_AUTOSTART_TIMEOUT", 60), \
     mock.patch.object(time, "sleep", return_value=None):
    called = {"start": False}
    real_startfile = os.startfile
    os.startfile = lambda p: called.__setitem__("start", True)
    try:
        r = b.ensure_started()
    finally:
        os.startfile = real_startfile
    check("T2d 未运行->自动拉起并恢复窗口->True",
          r is True and called["start"] and fg.called)
    check("T2d hwnd 已记录", b._hwnd == 100)

# T2e: 都未运行且超时（timeout=0，循环不进）-> False
b = make_broker()
with mock.patch.object(m, "find_window_by_title", return_value=0), \
     mock.patch.object(m, "_process_running_by_path", return_value=False), \
     mock.patch.object(m, "force_foreground", return_value=True), \
     mock.patch.object(m.config, "THS_AUTOSTART", True), \
     mock.patch.object(m.config, "THS_AUTOSTART_TIMEOUT", 0):
    called = {"start": False}
    real_startfile = os.startfile
    os.startfile = lambda p: called.__setitem__("start", True)
    try:
        r = b.ensure_started()
    finally:
        os.startfile = real_startfile
    check("T2e 超时未出现窗口->False 但已尝试启动", r is False and called["start"])


# ---------------- 汇总 ----------------
fails = [n for n, ok in passed if not ok]
print("\n==== %d/%d 通过 ====" % (len(passed) - len(fails), len(passed)))
if fails:
    print("失败项: " + ", ".join(fails))
    sys.exit(1)
print("ALL PASS")
