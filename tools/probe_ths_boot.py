# -*- coding: utf-8 -*-
"""同花顺启动过程探针（诊断"执行端拉起的同花顺假死/空白"）。

做两件事：
  1) 按执行端完全相同的方式启动同花顺（os.startfile），然后逐秒记录窗口状态：
     进程、每个顶层窗口的 类名/标题/可见/最小化/**IsHungAppWindow**/子控件数，
     以及关键词窗口是否出现、是否已渲染出下单控件(Edit 1032/1033/1034)、
     是否还停在登录窗口（有"登录"按钮）。
  2) 在指定时刻复现 easytrader `_close_prompt_windows()` 的行为
     （把所有可见 #32770 且标题 != "网上股票交易系统5.0" 的窗口 close 掉），
     对比之后窗口/进程的变化 —— 验证它是不是把同花顺自己关掉/弄坏的元凶。

用法：
    python tools/probe_ths_boot.py [--path <xiadan.exe>] [--seconds 75]
                                   [--close-at 8] [--no-close]
输出同时打印并写入 _ths_boot_probe.log（UTF-8）。
"""
import argparse
import os
import sys
import time

import win32api
import win32con
import win32gui
import win32process

KEYWORD = "网上股票交易系统"
MAIN_TITLE = "网上股票交易系统5.0"


def pids_of(path):
    target = os.path.normcase(os.path.abspath(path))
    seen = set()

    def cb(h, _):
        try:
            p = win32process.GetWindowThreadProcessId(h)[1]
            if p:
                seen.add(p)
        except Exception:
            pass
        return True

    try:
        win32gui.EnumWindows(cb, None)
    except Exception:
        pass
    hit = []
    for pid in seen:
        try:
            h = win32api.OpenProcess(0x0400 | 0x0010, False, pid)
            p = win32process.GetModuleFileNameEx(h, 0)
            win32api.CloseHandle(h)
            if os.path.normcase(p) == target:
                hit.append(pid)
        except Exception:
            continue
    return hit


def ths_windows(path):
    """同花顺安装目录下进程的所有顶层窗口。"""
    root = os.path.normcase(os.path.dirname(os.path.abspath(path)))
    exe_cache = {}
    out = []

    def cb(h, _):
        try:
            pid = win32process.GetWindowThreadProcessId(h)[1]
            if not pid:
                return True
            if pid not in exe_cache:
                try:
                    fh = win32api.OpenProcess(0x0400 | 0x0010, False, pid)
                    exe_cache[pid] = win32process.GetModuleFileNameEx(fh, 0)
                    win32api.CloseHandle(fh)
                except Exception:
                    exe_cache[pid] = ""
            exe = exe_cache[pid]
            if not exe or not os.path.normcase(exe).startswith(root):
                return True
            out.append({
                "hwnd": h,
                "pid": pid,
                "cls": win32gui.GetClassName(h),
                "title": win32gui.GetWindowText(h),
                "visible": bool(win32gui.IsWindowVisible(h)),
                "iconic": bool(win32gui.IsIconic(h)),
                "hung": bool(win32gui.IsHungAppWindow(h)),
                "children": _count_children(h),
                "buttons": _buttons(h),
                "edits": _edit_ids(h),
            })
        except Exception:
            pass
        return True

    try:
        win32gui.EnumWindows(cb, None)
    except Exception:
        pass
    return out


def _count_children(h):
    n = [0]

    def cb(c, _):
        n[0] += 1
        return True

    try:
        win32gui.EnumChildWindows(h, cb, None)
    except Exception:
        pass
    return n[0]


def _buttons(h):
    out = []

    def cb(c, _):
        try:
            if win32gui.GetClassName(c).lower() == "button":
                t = (win32gui.GetWindowText(c) or "").strip()
                if t:
                    out.append(t[:12])
        except Exception:
            pass
        return True

    try:
        win32gui.EnumChildWindows(h, cb, None)
    except Exception:
        pass
    return out


def _edit_ids(h):
    out = set()

    def cb(c, _):
        try:
            if win32gui.GetClassName(c).lower() == "edit":
                gwl_id = win32api.GetWindowLong(c, win32con.GWL_ID)
                out.add(int(gwl_id))
        except Exception:
            pass
        return True

    try:
        win32gui.EnumChildWindows(h, cb, None)
    except Exception:
        pass
    return sorted(out)


def snapshot(path, tag):
    pids = pids_of(path)
    wins = ths_windows(path) if pids else []
    main = [w for w in wins if w["title"] and KEYWORD in w["title"]]
    login = [w for w in wins if "登录" in w["buttons"]]
    lines = ["%-7s 进程=%s 顶层窗口=%d 关键词窗口=%d 登录窗口=%d"
             % (tag, pids or "无", len(wins), len(main), len(login))]
    for w in wins:
        lines.append("        hwnd=%s pid=%s cls=%-22s vis=%s icon=%s **hung=%s** 子控件=%d"
                     % (hex(w["hwnd"]), w["pid"], w["cls"], int(w["visible"]),
                        int(w["iconic"]), int(w["hung"]), w["children"]))
        lines.append("              标题=%r 按钮=%s 下单Edit=%s"
                     % (w["title"][:40], w["buttons"][:8], w["edits"]))
    return "\n".join(lines), wins


def close_prompt_windows(wins):
    """复现 easytrader._close_prompt_windows()：可见 #32770 且标题 != 主标题 -> close()。"""
    done = []
    for w in wins:
        if w["cls"] != "#32770":
            continue
        if not w["visible"]:
            continue
        if w["title"] == MAIN_TITLE:
            continue
        try:
            # pywinauto 的 close() 是 SendMessage(WM_CLOSE)；这里用 PostMessage
            # 避免对方卡死时把自己也阻塞住（诊断脚本要能跑完）
            win32gui.PostMessage(w["hwnd"], win32con.WM_CLOSE, 0, 0)
            done.append((hex(w["hwnd"]), w["title"], tuple(w["buttons"][:4])))
        except Exception as e:
            done.append((hex(w["hwnd"]), "POST失败:%s" % e, ()))
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default=r"D:\同花顺软件\同花顺\xiadan.exe")
    ap.add_argument("--seconds", type=int, default=75)
    ap.add_argument("--close-at", type=int, default=8)
    ap.add_argument("--no-close", action="store_true")
    ap.add_argument("--cwd", default=None,
                    help="启动时的工作目录；不给=继承本进程 cwd（执行端现状）；"
                         "给 'exe' 表示用 exe 所在目录（=双击/快捷方式的行为）")
    ap.add_argument("--no-launch", action="store_true", help="只观测不启动")
    args = ap.parse_args()

    cwd = args.cwd
    if cwd == "exe":
        cwd = os.path.dirname(os.path.abspath(args.path))

    buf = []

    def emit(s):
        print(s, flush=True)
        buf.append(s)

    emit("=" * 78)
    emit("同花顺启动探针  路径=%s" % args.path)
    emit("现在=%s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    emit("本进程 cwd=%s" % os.getcwd())
    emit("启动用 cwd=%s" % (cwd if cwd else "(继承，即 %s)" % os.getcwd()))
    emit("=" * 78)

    pre, _ = snapshot(args.path, "T-0")
    emit(pre)
    if pids_of(args.path):
        emit("!! 同花顺已在运行，本次探针会受干扰（建议先关闭同花顺）")

    t0 = time.time()
    if args.no_launch:
        emit("[0.0s] --no-launch：只观测")
    elif cwd:
        emit("[%.1fs] os.startfile 启动（cwd=%s）: %s" % (0, cwd, args.path))
        os.startfile(args.path, cwd=cwd)
    else:
        emit("[%.1fs] os.startfile 启动（继承 cwd）: %s" % (0, args.path))
        os.startfile(args.path)
    closed = False
    while time.time() - t0 < args.seconds:
        t = time.time() - t0
        s, wins = snapshot(args.path, "T+%.0fs" % t)
        emit(s)
        if (not closed) and (not args.no_close) and t >= args.close_at:
            closed = True
            emit("---- 复现 easytrader._close_prompt_windows() @T+%.0fs ----" % t)
            for rec in close_prompt_windows(wins):
                emit("     close window %s  标题=%r 按钮=%s" % (rec[0], rec[1], list(rec[2])))
        time.sleep(2)

    emit("=" * 78)
    emit("结束。最终状态：")
    emit(snapshot(args.path, "END")[0])
    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "_ths_boot_probe.log")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(buf))
    print("日志已写入: %s" % out)


if __name__ == "__main__":
    sys.exit(main())
