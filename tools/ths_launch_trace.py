# -*- coding: utf-8 -*-
"""「行情主程序 + F12」启动路径全流程跟踪器（只读观察 / 可选真拉起）。

为什么需要它（2026-09-13 用户反馈"重启执行端后能启动行情，还是无法拉起下单程序"）：
    执行端日志只说"F12 已拉起交易窗口"，却没人知道**那个窗口到底是谁的**：
      * 是真正的 xiadan.exe 交易模块？—— 那 easytrader 应该能连上；
      * 还是 hexin.exe 自己在启动过程中冒出来的同标题窗口（假象）？—— 那 easytrader
        必然报 `Process "...xiadan.exe" not found!`。
    日志里"F12 已拉起"与 60 秒后"未见交易窗口"自相矛盾，只有把窗口按 0.5 秒
    粒度连**归属进程/EXE 路径**一起录下来才能定性。

用法：
    python tools/ths_launch_trace.py --watch 120
        只观察 120 秒，不改动任何进程/窗口（可在执行端自己拉起同花顺时并行跑）。

    python tools/ths_launch_trace.py --watch 150 --launch
        自己走一遍真实的 _launch_main()（hexin.exe -> 就绪门槛 -> F12），全程采样。

    python tools/ths_launch_trace.py --watch 120 --kill-ths-first
        先把现存 hexin/xiadan 结束掉，拿到干净现场（只动同花顺，不动中转/执行端）。

输出：每条变化一行（时间 + 事件 + hwnd + 归属EXE + 类名 + 标题 + 尺寸 + 可见性
+ 消息循环是否响应），末尾给出结论段：交易窗口出现过几次、分别属于哪个 EXE、
xia dan 进程是否存在过及其存活区间。
"""
import argparse
import ctypes
import os
import sys
import time
from ctypes import wintypes
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8')

k32 = ctypes.windll.kernel32
user32 = ctypes.windll.user32
psapi = ctypes.windll.psapi
psapi.EnumProcesses.argtypes = [ctypes.c_void_p, wintypes.DWORD,
                                ctypes.POINTER(wintypes.DWORD)]
psapi.EnumProcesses.restype = wintypes.BOOL

TITLE_KEYWORD = "网上股票交易系统"
XIADAN = r"D:\同花顺软件\同花顺\xiadan.exe"
HEXIN = r"D:\同花顺软件\同花顺\hexin.exe"

_t0 = time.time()


def ts():
    return '%7.1fs' % (time.time() - _t0)


def now():
    return datetime.now().strftime('%H:%M:%S.%f')[:-3]


# ---------------------------------------------------------------- 进程枚举
def all_procs():
    """返回 {pid: exe路径}（走 EnumProcesses，不依赖被沙箱拦截的 wmic/psutil）。"""
    MAX = 2048
    arr = (wintypes.DWORD * MAX)()
    need = wintypes.DWORD()
    psapi.EnumProcesses(ctypes.byref(arr), ctypes.sizeof(arr), ctypes.byref(need))
    n = need.value // ctypes.sizeof(wintypes.DWORD)
    out = {}
    for i in range(n):
        p = arr[i]
        if not p:
            continue
        h = k32.OpenProcess(0x1000 | 0x0400, False, p)      # QUERY_INFORMATION|VM_READ
        if not h:
            continue
        try:
            b = ctypes.create_unicode_buffer(1024)
            if psapi.GetModuleFileNameExW(h, None, b, 1024):
                out[p] = b.value
        finally:
            k32.CloseHandle(h)
    return out


def pids_of(procs, exe_path):
    t = os.path.normcase(os.path.abspath(exe_path))
    return sorted(p for p, e in procs.items()
                  if os.path.normcase(os.path.abspath(e)) == t)


# ---------------------------------------------------------------- 窗口枚举
def all_windows(procs):
    """全部顶层窗口 -> [(hwnd, pid, exe, class, title, rect, vis, iconic)]。

    用 pywin32（与 broker_ths 同一套通路）：ctypes 直接调 EnumWindows 需要
    构造 WNDENUMPROC，容易 TypeError；win32gui 已经封装好回调转换。
    """
    import win32gui
    import win32process
    out = []

    def _cb(h, _):
        try:
            if not win32gui.IsWindow(h):
                return True
            pid = win32process.GetWindowThreadProcessId(h)[1]
            l, t, r, b = win32gui.GetWindowRect(h)
            out.append((h, pid, procs.get(pid, '?'), win32gui.GetClassName(h),
                        win32gui.GetWindowText(h), (max(0, r - l), max(0, b - t)),
                        bool(win32gui.IsWindowVisible(h)), bool(win32gui.IsIconic(h))))
        except Exception:
            pass
        return True

    win32gui.EnumWindows(_cb, None)
    return out


def responds(hwnd, timeout_ms=700):
    try:
        user32.SendMessageTimeoutW.argtypes = [
            wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t,
            wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
        user32.SendMessageTimeoutW.restype = ctypes.c_ssize_t
        res = ctypes.c_size_t(0)
        ok = user32.SendMessageTimeoutW(wintypes.HWND(hwnd), 0, 0, 0,
                                        0x0001 | 0x0002, int(timeout_ms),
                                        ctypes.byref(res))
        return bool(ok)
    except Exception:
        return True


def kw_windows(wins):
    """标题含交易关键字的顶层窗口。"""
    return [w for w in wins if w[4] and TITLE_KEYWORD in w[4]]


def hexin_windows(wins, procs):
    """属于 hexin.exe 的顶层窗口（行情主程序自己的窗口，标题通常不含关键字）。"""
    ids = set(pids_of(procs, HEXIN))
    return [w for w in wins if w[1] in ids]


def fmt_win(w):
    hwnd, pid, exe, cls, title, (cw, ch), vis, ico = w
    return ('hwnd=%-8s pid=%-6s %-12s class=%-28s %sx%-5s %s%s title=%r resp=%s'
            % (hwnd, pid, os.path.basename(exe or '?'), cls[:28], cw, ch,
               '可见' if vis else '隐藏', '/最小化' if ico else '',
               title[:38], responds(hwnd)))


def hr(t=''):
    print('\n' + '=' * 78)
    if t:
        print(t)
        print('=' * 78)


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--watch', type=int, default=120, help='采样总时长(秒)')
    ap.add_argument('--interval', type=float, default=0.5, help='采样间隔(秒)')
    ap.add_argument('--launch', action='store_true',
                    help='由本脚本真拉起（走 broker_ths._launch_main）')
    ap.add_argument('--kill-ths-first', action='store_true',
                    help='先结束现存 hexin/xiadan，拿干净现场')
    ap.add_argument('--json', default='', help='把事件流另存为文件')
    a = ap.parse_args()

    hr('RelayGo 同花顺启动路径跟踪器')
    print('  关键字   %r' % TITLE_KEYWORD)
    print('  采样间隔 %ss   总时长 %ss' % (a.interval, a.watch))
    print('  模式     %s' % ('真拉起(_launch_main)' if a.launch else '只观察（不改动任何东西）'))

    procs = all_procs()
    print('  会话     %s' % _session_of(os.getpid()))
    print('  hexin    %s' % (pids_of(procs, HEXIN) or '未运行'))
    print('  xiadan   %s' % (pids_of(procs, XIADAN) or '未运行'))

    if a.kill_ths_first:
        for path in (XIADAN, HEXIN):
            for p in pids_of(all_procs(), path):
                print('  结束进程 pid=%s %s' % (p, os.path.basename(path)))
                h = k32.OpenProcess(0x0001, False, p)        # PROCESS_TERMINATE
                if h:
                    k32.TerminateProcess(h, 0)
                    k32.CloseHandle(h)
        time.sleep(3)

    log = open(a.json, 'w', encoding='utf-8') if a.json else None

    def emit(line):
        print(line, flush=True)
        if log:
            log.write(line + '\n')
            log.flush()

    # 基线快照
    procs = all_procs()
    wins = all_windows(procs)
    seen_kw = {w[0]: w for w in kw_windows(wins)}
    seen_hex = {w[0]: w for w in hexin_windows(wins, procs)}
    prev_hexin_pids = pids_of(procs, HEXIN)
    prev_xiadan_pids = pids_of(procs, XIADAN)

    hr('基线：行情主程序(hexin.exe)的顶层窗口')
    if seen_hex:
        for w in sorted(seen_hex.values(), key=lambda x: -x[5][0] * x[5][1]):
            emit('  ' + fmt_win(w))
    else:
        emit('  （无）')
    hr('基线：标题含关键字的窗口')
    if seen_kw:
        for w in seen_kw.values():
            emit('  ' + fmt_win(w))
    else:
        emit('  （无）')

    # ---- 可选：脚本自己拉起 ----
    if a.launch:
        hr('执行 _launch_main()（真拉起）')
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        '..', 'executor'))
        import broker_ths
        import config
        b = broker_ths.ThsBroker(config.THS_XIADAN_PATH)
        t1 = time.time()
        try:
            ok = b._launch_main()
            emit('  _launch_main() = %s（耗时 %.1f 秒）' % (ok, time.time() - t1))
        except Exception as e:
            emit('  _launch_main() 抛异常: %r' % (e,))

    # ---- 采样循环 ----
    hr('采样中（变化即打印）')
    t_end = time.time() + a.watch
    kw_events = []          # (时刻, 'APPEAR'/'GONE', 窗口元组)
    xiadan_life = []        # (时刻, 'START'/'EXIT', pid)
    hexin_life = []
    last_beat = 0.0
    while time.time() < t_end:
        procs = all_procs()
        wins = all_windows(procs)

        for p in pids_of(procs, HEXIN):
            if p not in prev_hexin_pids:
                emit('[%s] %s  hexin.exe 启动 pid=%s' % (ts(), now(), p))
                hexin_life.append((now(), 'START', p))
        for p in prev_hexin_pids:
            if p not in pids_of(procs, HEXIN):
                emit('[%s] %s  hexin.exe 退出 pid=%s' % (ts(), now(), p))
                hexin_life.append((now(), 'EXIT', p))
        prev_hexin_pids = pids_of(procs, HEXIN)

        for p in pids_of(procs, XIADAN):
            if p not in prev_xiadan_pids:
                emit('[%s] %s  *** xiadan.exe 启动 pid=%s ***' % (ts(), now(), p))
                xiadan_life.append((now(), 'START', p))
        for p in prev_xiadan_pids:
            if p not in pids_of(procs, XIADAN):
                emit('[%s] %s  *** xiadan.exe 退出 pid=%s ***' % (ts(), now(), p))
                xiadan_life.append((now(), 'EXIT', p))
        prev_xiadan_pids = pids_of(procs, XIADAN)

        cur_kw = {w[0]: w for w in kw_windows(wins)}
        for hwnd, w in cur_kw.items():
            if hwnd not in seen_kw:
                emit('[%s] %s  关键字窗口 APPEAR  %s' % (ts(), now(), fmt_win(w)))
                kw_events.append((now(), 'APPEAR', w))
        for hwnd, w in seen_kw.items():
            if hwnd not in cur_kw:
                emit('[%s] %s  关键字窗口 GONE    hwnd=%s pid=%s %s title=%r'
                     % (ts(), now(), hwnd, w[1], os.path.basename(w[2] or '?'), w[4][:38]))
                kw_events.append((now(), 'GONE', w))
        seen_kw = cur_kw

        cur_hex = {w[0]: w for w in hexin_windows(wins, procs)}
        for hwnd, w in cur_hex.items():
            if hwnd not in seen_hex:
                emit('[%s] %s  行情窗口 APPEAR    %s' % (ts(), now(), fmt_win(w)))
        for hwnd, w in seen_hex.items():
            if hwnd not in cur_hex:
                emit('[%s] %s  行情窗口 GONE      hwnd=%s pid=%s %s title=%r'
                     % (ts(), now(), hwnd, w[1], os.path.basename(w[2] or '?'), w[4][:38]))
        seen_hex = cur_hex

        if time.time() - last_beat >= 15:
            last_beat = time.time()
            kh = [w for w in sorted(cur_hex.values(), key=lambda x: -x[5][0] * x[5][1])]
            emit('[%s] 心跳  hexin=%s xiadan=%s 行情窗口=%d 关键字窗口=%d%s'
                 % (ts(), prev_hexin_pids or '-', prev_xiadan_pids or '-',
                    len(kh), len(cur_kw),
                    ('  最大行情窗口: %sx%s vis=%s resp=%s'
                     % (kh[0][5][0], kh[0][5][1], kh[0][6], responds(kh[0][0]))) if kh else ''))
        time.sleep(a.interval)

    # ---- 结论 ----
    hr('结论')
    print('  xiadan.exe 生命周期：')
    if xiadan_life:
        for t, k, p in xiadan_life:
            print('    %s  %s  pid=%s' % (t, k, p))
    else:
        print('    **全程未出现 xiadan.exe 进程** —— F12 没真正拉起交易模块')
    print()
    print('  hexin.exe 生命周期：%s' % (['%s %s pid=%s' % x for x in hexin_life] or '（无变化）'))
    print()
    print('  标题含关键字的窗口事件（%d 条）：' % len(kw_events))
    for t, k, w in kw_events:
        print('    %s  %-6s hwnd=%-8s pid=%-6s %-12s class=%-24s %sx%s 可见=%s'
              % (t, k, w[0], w[1], os.path.basename(w[2] or '?'), w[3][:24],
                 w[5][0], w[5][1], w[6]))
    if log:
        log.close()


def _session_of(pid):
    s = wintypes.DWORD()
    k32.ProcessIdToSessionId(pid, ctypes.byref(s))
    return s.value


if __name__ == '__main__':
    main()
