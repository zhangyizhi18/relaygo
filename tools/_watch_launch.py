# -*- coding: utf-8 -*-
"""冷启动观测器：采样 hexin/xiadan 进程 + 交易窗口 + 执行端日志新增行。

用法: venv\\Scripts\\python.exe tools/_watch_launch.py <秒数> <executor日志路径> <输出文件>
"""
import sys
import time
import datetime

import win32gui
import win32process
import win32api

DUR = int(sys.argv[1]) if len(sys.argv) > 1 else 100
LOG = sys.argv[2] if len(sys.argv) > 2 else r".workbuddy\tmp_exec_D.log"
OUT = sys.argv[3] if len(sys.argv) > 3 else r".workbuddy\tmp_watch.log"


def pids(pathkey):
    out = set()

    def cb(h, _):
        try:
            pid = win32process.GetWindowThreadProcessId(h)[1]
            hp = win32api.OpenProcess(0x1000 | 0x0400, False, pid)
            try:
                exe = win32process.GetModuleFileNameEx(hp, 0)
            finally:
                win32api.CloseHandle(hp)
        except Exception:
            return
        if pathkey in exe.lower():
            out.add(pid)

    win32gui.EnumWindows(cb, None)
    return out


def trading_windows():
    found = []
    kw = "\u7f51\u4e0a\u80a1\u7968\u4ea4\u6613\u7cfb\u7edf5.0"

    def cb(h, _):
        try:
            t = win32gui.GetWindowText(h) or ""
        except Exception:
            return
        if kw in t:
            try:
                pid = win32process.GetWindowThreadProcessId(h)[1]
                l, t2, r, b = win32gui.GetWindowRect(h)
                vis = win32gui.IsWindowVisible(h)
                ico = win32gui.IsIconic(h)
            except Exception:
                pid, l, r, b, vis, ico = 0, 0, 0, 0, 0, 0
            found.append((h, pid, r - l, b - t2, vis, ico))

    win32gui.EnumWindows(cb, None)
    return found


def readlog(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read().splitlines()
    except Exception:
        return []


f = open(OUT, "w", encoding="utf-8")
seen = 0
prev_hx, prev_xd = set(), set()
t0 = time.time()
last_beat = 0.0
while time.time() - t0 < DUR:
    now = time.time()
    el = now - t0
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    hx, xd = pids("hexin.exe"), pids("xiadan.exe")
    lines = readlog(LOG)
    if lines and seen < len(lines):
        for l in lines[seen:]:
            f.write("[%6.1fs] %s  LOG %s\n" % (el, ts, l))
        seen = len(lines)
    if hx != prev_hx:
        f.write("[%6.1fs] %s  *** hexin.exe -> %s ***\n" % (el, ts, sorted(hx)))
        prev_hx = hx
    if xd != prev_xd:
        f.write("[%6.1fs] %s  *** xiadan.exe -> %s ***\n" % (el, ts, sorted(xd)))
        prev_xd = xd
    if now - last_beat >= 4:
        last_beat = now
        f.write("[%6.1fs] %s  心跳 hexin=%s xiadan=%s 交易窗口=%s\n"
                % (el, ts, sorted(hx), sorted(xd), trading_windows()))
    f.flush()
    time.sleep(1.0)

# 收尾：把剩余日志行也写进去
lines = readlog(LOG)
if seen < len(lines):
    for l in lines[seen:]:
        f.write("[%6.1fs] %s  LOG %s\n" % (time.time() - t0, ts, l))
f.write("=== 观测结束 ===\n")
f.close()
print("done ->", OUT)
