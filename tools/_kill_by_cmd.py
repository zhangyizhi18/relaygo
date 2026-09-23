# -*- coding: utf-8 -*-
"""按命令行匹配结束进程（用于重启执行端/中转服务，避免误杀其它 python）。

用法：
    venv\\Scripts\\python.exe tools\\_kill_by_cmd.py executor/main.py
"""
import ctypes
import ctypes.wintypes as w
import sys
import time

sys.path.insert(0, __file__.rsplit("\\", 1)[0])
from _pid_info import cmdline, exe_of          # noqa: E402

k32 = ctypes.windll.kernel32
psapi = ctypes.windll.psapi
psapi.EnumProcesses.argtypes = [ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD)]
psapi.EnumProcesses.restype = w.BOOL


def pids_matching(needle):
    arr = (w.DWORD * 4096)()
    need = w.DWORD()
    psapi.EnumProcesses(arr, ctypes.sizeof(arr), ctypes.byref(need))
    n = need.value // ctypes.sizeof(w.DWORD)
    out = []
    for i in range(n):
        p = arr[i]
        if not p or p == k32.GetCurrentProcessId():
            continue
        cl = cmdline(p)
        if cl and needle.lower() in cl.lower():
            out.append((p, cl))
    return out


def main():
    if len(sys.argv) < 2:
        print("用法: _kill_by_cmd.py <命令行子串>")
        return 2
    needle = sys.argv[1]
    hits = pids_matching(needle)
    if not hits:
        print("没有匹配 %r 的进程" % needle)
        return 0
    for p, cl in hits:
        h = k32.OpenProcess(0x0001, False, p)      # PROCESS_TERMINATE
        if h:
            k32.TerminateProcess(h, 0)
            k32.CloseHandle(h)
            print("已结束 PID=%d  %s" % (p, cl[:120]))
        else:
            print("打开失败 PID=%d（可能已退出）" % p)
    # 等一会儿确认真的退出
    deadline = time.time() + 8
    while time.time() < deadline:
        if not pids_matching(needle):
            print("确认已全部退出")
            return 0
        time.sleep(0.5)
    print("警告：仍有残留进程: %s" % [p for p, _ in pids_matching(needle)])
    return 1


if __name__ == "__main__":
    sys.exit(main())
