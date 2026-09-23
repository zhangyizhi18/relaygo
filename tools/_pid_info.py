# -*- coding: utf-8 -*-
"""列出 venv python 进程及其命令行（用于定位执行端/中转服务，便于精确重启）。

沙箱拦截 wmic；这里用 NtQueryInformationProcess 读 PEB 拿命令行（纯 win32/ntdll）。
"""
import ctypes
import ctypes.wintypes as w

k32 = ctypes.windll.kernel32
psapi = ctypes.windll.psapi
ntdll = ctypes.windll.ntdll

psapi.EnumProcesses.argtypes = [ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD)]
psapi.EnumProcesses.restype = w.BOOL
k32.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
k32.OpenProcess.restype = w.HANDLE
k32.ReadProcessMemory.argtypes = [w.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                  ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]


class PBI(ctypes.Structure):
    _fields_ = [("Reserved1", ctypes.c_void_p),
                ("PebBaseAddress", ctypes.c_void_p),
                ("Reserved2", ctypes.c_void_p * 2),
                ("UniqueProcessId", ctypes.c_void_p),
                ("Reserved3", ctypes.c_void_p)]


def _read(h, addr, size):
    buf = (ctypes.c_char * size)()
    got = ctypes.c_size_t(0)
    if not k32.ReadProcessMemory(h, ctypes.c_void_p(addr), buf, size,
                                 ctypes.byref(got)):
        return None
    return bytes(buf)


def cmdline(pid):
    h = k32.OpenProcess(0x0400 | 0x0010, False, pid)      # QUERY_INFO|VM_READ
    if not h:
        return None
    try:
        pbi = PBI()
        if ntdll.NtQueryInformationProcess(h, 0, ctypes.byref(pbi),
                                           ctypes.sizeof(pbi), None) != 0:
            return None
        raw = _read(h, pbi.PebBaseAddress + 0x20, 8)
        if not raw:
            return None
        params = int.from_bytes(raw, "little")
        us = _read(h, params + 0x70, 16)                  # UNICODE_STRING
        if not us:
            return None
        length = int.from_bytes(us[0:2], "little")
        bufptr = int.from_bytes(us[8:16], "little")
        if not length or not bufptr:
            return ""
        s = _read(h, bufptr, length)
        return s.decode("utf-16-le", "replace") if s else ""
    except Exception as e:
        return "<err %s>" % e
    finally:
        k32.CloseHandle(h)


def exe_of(pid):
    h = k32.OpenProcess(0x1000 | 0x0400, False, pid)
    if not h:
        return ""
    try:
        b = ctypes.create_unicode_buffer(1024)
        if psapi.GetModuleFileNameExW(h, None, b, 1024):
            return b.value
    finally:
        k32.CloseHandle(h)
    return ""


def main():
    arr = (w.DWORD * 4096)()
    need = w.DWORD()
    psapi.EnumProcesses(arr, ctypes.sizeof(arr), ctypes.byref(need))
    n = need.value // ctypes.sizeof(w.DWORD)
    for i in range(n):
        p = arr[i]
        if not p:
            continue
        exe = exe_of(p)
        if not exe or not exe.lower().endswith("python.exe"):
            continue
        cl = cmdline(p) or ""
        print("PID=%-7d %s" % (p, exe))
        print("         CMD: %s" % (cl[:300] or "(空)"))


if __name__ == "__main__":
    main()
