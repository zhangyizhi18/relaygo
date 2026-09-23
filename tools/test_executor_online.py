# -*- coding: utf-8 -*-
"""执行端「在线状态与同花顺解耦」+「重启同花顺（在运行则重启/未运行则启动）」离线自测。

对应 2026-09-12 用户需求：
  * 执行端启动后，不管同花顺下单有没有被启动 / 识别，控制台都应显示执行端在线；
  * 点「重启同花顺」：同花顺在运行则重启、未运行则启动 —— 可在控制台随时恢复，不必到现场。

全部走 mock，不连中转、不碰同花顺、不下单。用法：
    venv/Scripts/python.exe tools/test_executor_online.py
"""
import os
import sys
import threading
import time
import types
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "executor"))
os.environ.setdefault("JQ_ENVFILE_NOCREATE", "1")   # 不要因自测去写 .env

import config        # noqa: E402
import main as M     # noqa: E402
import tasks as T    # noqa: E402
import broker_ths as B  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    flag = "  [OK]   " if cond else "  [FAIL] "
    print(flag + name + (("   <- " + str(extra)) if extra else ""))


class FakeBroker(object):
    """可控适配器替身（不依赖同花顺）。"""

    name = "fake"

    def __init__(self, connect_ok=True, health=(True, "fake 连接正常")):
        self.user = None
        self.connect_calls = 0
        self.health_calls = 0
        self.connect_ok = connect_ok
        self.health = health
        self.restart_called = 0

    def connect(self, verbose=True):
        self.connect_calls += 1
        if not self.connect_ok:
            raise RuntimeError("连接失败(测试)")

    def health_check(self):
        self.health_calls += 1
        return self.health

    def restart_ths(self):
        self.restart_called += 1
        return {"ok": True, "message": "已重启同花顺下单程序并连接成功"}

    def query_balance(self):
        return {"可用金额": 1.0}


class FakeRelay(object):
    def __init__(self):
        self.names = []

    def heartbeat(self, name, timeout=15):
        self.names.append(name)
        return {}


class NoVerboseBroker(FakeBroker):
    """模拟不接受 verbose 参数的适配器（miniqmt/thsauto 风格）。"""

    def connect(self):
        self.connect_calls += 1


# ==================== G1 build_broker：只构造、不连接 ====================

print("\n== G1 build_broker（构造不再阻塞等待登录） ==")

with mock.patch.object(M.config, "MODE", "dry_run"):
    check("G1a dry_run -> None（模拟模式不下单）", M.build_broker() is None)

with mock.patch.object(M.config, "MODE", "ths"), \
     mock.patch.object(M.config, "THS_XIADAN_PATH", r"D:\x\xiadan.exe"):
    _b = M.build_broker()
    check("G1b ths -> 返回 ThsBroker 实例且未连接(user is None)",
          isinstance(_b, B.ThsBroker) and _b.user is None)

with mock.patch.object(M.config, "MODE", "bad_mode"):
    try:
        M.build_broker()
        _raised = False
    except ValueError:
        _raised = True
    check("G1c 非法 MODE 仍抛 ValueError（由 main 兜住，不再让进程退出）", _raised)

check("G1d 旧函数 create_broker 已无定义（避免误用阻塞版）",
      not hasattr(M, "create_broker"))

# ==================== G2 BrokerSlot 初始态 ====================

print("\n== G2 BrokerSlot 初始状态 ==")

with mock.patch.object(M.config, "MODE", "dry_run"):
    check("G2a dry_run -> 天然就绪", M.BrokerSlot().ready is True)

with mock.patch.object(M.config, "MODE", "ths"):
    _s = M.BrokerSlot()
    check("G2b ths -> 初始未就绪，文案为『同花顺未就绪』",
          _s.ready is False and "未就绪" in _s.readable(), _s.readable())

# ==================== G3 后台心跳：与券商状态无关 ====================

print("\n== G3 心跳线程独立于券商连接状态 ==")

_fr = FakeRelay()
_slot = M.BrokerSlot()
_slot.ready = False            # 刻意"同花顺没连上"
with mock.patch.object(M.config, "MODE", "ths"):
    _stop = threading.Event()
    _th = threading.Thread(target=M.heartbeat_loop, args=(_slot, _stop, _fr),
                           daemon=True)
    _th.start()
    time.sleep(0.5)
    _stop.set()
    _th.join(timeout=3)

check("G3a 券商未就绪时心跳照发（控制台仍显示执行端在线）", len(_fr.names) >= 1)
check("G3b 心跳文案带上券商状态，便于一眼看出问题",
      bool(_fr.names) and "未就绪" in _fr.names[0], _fr.names[:1])
check("G3c 心跳线程可正常停止（不卡死）", not _th.is_alive())

# ==================== G4 ensure_broker_ready：自愈不退出 ====================

print("\n== G4 ensure_broker_ready（连接失败不退出、限频重试） ==")

M.ensure_broker_ready(M.BrokerSlot())          # broker=None：不应抛异常
check("G4a broker=None 时安全跳过（dry_run）", True)

_fb = FakeBroker(connect_ok=False)
with mock.patch.object(M.config, "MODE", "ths"):
    _s = M.BrokerSlot()            # ths 模式：初始未就绪，需先 connect
    _s.broker = _fb
    with mock.patch.object(M.config, "BROKER_RETRY_INTERVAL", 30):
        M.ensure_broker_ready(_s)
        _n1 = _fb.connect_calls
        M.ensure_broker_ready(_s)                  # 间隔内再调：应被限频跳过
check("G4b connect 失败不抛出且置为未就绪",
      _s.ready is False and "测试" in _s.last_err and _n1 == 1, _s.last_err)
check("G4c 限频生效（重试间隔内不重复 connect）",
      _fb.connect_calls == _n1 and _n1 == 1, _fb.connect_calls)

with mock.patch.object(M.config, "MODE", "ths"):
    _s = M.BrokerSlot()
    _fb = FakeBroker()
    _s.broker = _fb
    with mock.patch.object(M.config, "BROKER_RETRY_INTERVAL", 30):
        M.ensure_broker_ready(_s)
check("G4d connect+自检通过 -> ready=True 且清除错误",
      _s.ready is True and _s.last_err == "" and _fb.connect_calls == 1)

_fb.health = (False, "连接异常(测试)")
_s.next_try = 0.0
with mock.patch.object(M.config, "BROKER_RETRY_INTERVAL", 30):
    M.ensure_broker_ready(_s)
check("G4e 已就绪后掉线 -> 自动转回未就绪（可再重连）",
      _s.ready is False and "连接异常" in _s.last_err, _s.last_err)

_nb = NoVerboseBroker()
M._connect_broker(_nb)
check("G4f _connect_broker 兼容不带 verbose 的适配器", _nb.connect_calls == 1)

# ==================== G5 TaskRunner：未就绪时的指引与豁免 ====================

print("\n== G5 控制台任务：未就绪给指引，恢复手段豁免 ==")

_fb = FakeBroker()
_probe_off = lambda: (False, "连接超时")          # noqa: E731
_r = T.TaskRunner(_fb, guard=None, dry_run=False, ready_probe=_probe_off)

_ok, _res, _msg = _r.handle({"id": 1, "kind": "query_balance", "payload": {}})
check("G5a 未就绪查资金 -> 被拦截且指引含『重启同花顺』",
      _ok is False and "重启同花顺" in _msg, _msg)

_ok, _res, _msg = _r.handle({"id": 2, "kind": "place_order",
                             "payload": {"security": "600519.XSHG", "side": "buy",
                                         "amount": 100, "price": 10}})
check("G5b 未就绪下单 -> 被拦截（不会去操作空连接）",
      _ok is False and "重启同花顺" in _msg, _msg)

_ok, _res, _msg = _r.handle({"id": 3, "kind": "clear_form", "payload": {}})
check("G5c 未就绪清场 -> 被拦截", _ok is False, _msg)

with mock.patch.object(T.config, "MODE", "ths"):
    _ok, _res, _msg = _r.handle({"id": 4, "kind": "restart_ths", "payload": {}})
check("G5d restart_ths 豁免前置检查（同花顺挂了时的唯一自救手段）",
      _ok is True and _fb.restart_called == 1, _msg)

with mock.patch.object(T.config, "MODE", "ths"):
    _ok, _res, _msg = _r.handle({"id": 5, "kind": "guard_reset", "payload": {}})
check("G5e 解除熔断也不受『券商就绪』限制", _ok is True, _msg)

_r2 = T.TaskRunner(_fb, guard=None, dry_run=False, ready_probe=lambda: (True, ""))
_ok, _res, _msg = _r2.handle({"id": 6, "kind": "query_balance", "payload": {}})
check("G5f 就绪后正常放行", _ok is True, _msg)

_r3 = T.TaskRunner(None, guard=None, dry_run=True, ready_probe=_probe_off)
_ok, _res, _msg = _r3.handle({"id": 7, "kind": "query_balance", "payload": {}})
check("G5g dry_run 模式不受前置检查影响（照旧返回模拟数据）",
      _ok is True and "dry_run" in _msg, _msg)

_r4 = T.TaskRunner(None, guard=None, dry_run=False, ready_probe=_probe_off)
_ok, _res, _msg = _r4.handle({"id": 8, "kind": "restart_ths", "payload": {}})
check("G5h 适配器构造失败(非 dry_run) -> restart 如实报错，不假装成功",
      _ok is False and "适配器" in _msg, _msg)

# ==================== G6 restart_ths：存在则重启 / 不存在则启动 ====================

print("\n== G6 restart_ths（不依赖已有连接） ==")

_CFG = types.SimpleNamespace(THS_AUTOSTART_TIMEOUT=0, THS_RESTART_WAIT=1,
                             THS_LAUNCH_MODE="main_f12",
                             THS_MAIN_PATH=r"D:\同花顺软件\同花顺\hexin.exe",
                             THS_MAIN_WAIT=1, THS_F12_RETRY=1,
                             # F12 就绪门槛相关（2026-09-13 新增；broker_ths 用
                             # getattr 容错读取，这里补全是为了让桩贴近真实配置）
                             THS_MAIN_MIN_AREA=120000, THS_MAIN_READY_STABLE=3,
                             THS_F12_TOTAL_WAIT=30, THS_F12_PER_WAIT=5,
                             THS_MAIN_EXIT_WAIT=15)


def _broker():
    b = B.ThsBroker(r"D:\同花顺软件\同花顺\xiadan.exe")
    b.user = None          # 刻意"从未连接过"（旧实现在此会崩）
    return b


# a) main_f12 进程在运行 -> 同时结束交易模块与行情主程序，再完整重拉
#    2026-09-13 用户要求：重启执行端时应把行情和下单软件一起重启，而不是只重拉交易模块。
_killed = []
with mock.patch.object(B, "_pids_of_xiadan", side_effect=[[1234], [], []]), \
     mock.patch.object(B, "_kill_by_path",
                       side_effect=lambda p: (_killed.append(p), [1234])[1]) as _mk, \
     mock.patch.object(B.ThsBroker, "_launch_main",
                       lambda self, force_restart_main=False: True), \
     mock.patch.object(B.ThsBroker, "wait_until_ready", return_value=(True, "ok")), \
     mock.patch.object(B.ThsBroker, "close_captcha_dialog",
                       side_effect=AssertionError("不应依赖已连接会话")), \
     mock.patch.object(B, "config", _CFG), \
     mock.patch.object(B, "find_window_by_title", return_value=0):
    _r = _broker().restart_ths()
check("G6a main_f12 同花顺在运行 -> 同时结束交易模块与行情主程序、完整重拉",
      _r["ok"] and _mk.called
      and _CFG.THS_MAIN_PATH in _killed and "已重启" in _r["message"],
      (_r, _killed))

# b) 进程不存在 -> 不结束进程、直接启动（完整重拉）
_killed = []
with mock.patch.object(B, "_pids_of_xiadan", return_value=[]), \
     mock.patch.object(B, "_kill_by_path",
                       side_effect=lambda p: (_killed.append(p), [1234])[1]) as _mk2, \
     mock.patch.object(B.ThsBroker, "_launch_main",
                       lambda self, force_restart_main=False: True), \
     mock.patch.object(B.ThsBroker, "wait_until_ready", return_value=(True, "ok")), \
     mock.patch.object(B.ThsBroker, "close_captcha_dialog",
                       side_effect=AssertionError("不应依赖已连接会话")), \
     mock.patch.object(B, "config", _CFG), \
     mock.patch.object(B, "find_window_by_title", return_value=0):
    _r = _broker().restart_ths()
check("G6b 同花顺未运行 -> 不结束进程、直接完整启动，返回『已启动』",
      _r["ok"] and (not _killed) and "已启动" in _r["message"], (_r, _killed))

# c) 进程探测失败 -> 跳过结束进程，但仍强制启动
_killed = []
with mock.patch.object(B, "_pids_of_xiadan", side_effect=RuntimeError("win32 异常")), \
     mock.patch.object(B, "_kill_by_path",
                       side_effect=lambda p: (_killed.append(p), [1234])[1]) as _mk3, \
     mock.patch.object(B.ThsBroker, "_launch_main",
                       lambda self, force_restart_main=False: True), \
     mock.patch.object(B.ThsBroker, "wait_until_ready", return_value=(True, "ok")), \
     mock.patch.object(B.ThsBroker, "close_captcha_dialog",
                       side_effect=AssertionError("不应依赖已连接会话")), \
     mock.patch.object(B, "config", _CFG), \
     mock.patch.object(B, "find_window_by_title", return_value=0):
    _r = _broker().restart_ths()
check("G6c 探测失败 -> 跳过结束进程但仍强制启动（不因『识别不到』什么都不做）",
      _r["ok"] and (not _killed), (_r, _killed))

# d) 等待就绪超时 -> 如实报错
with mock.patch.object(B, "_pids_of_xiadan", return_value=[]), \
     mock.patch.object(B.ThsBroker, "_launch_main",
                       lambda self, force_restart_main=False: True), \
     mock.patch.object(B.ThsBroker, "wait_until_ready",
                       return_value=(False, "未登录")), \
     mock.patch.object(B.ThsBroker, "close_captcha_dialog",
                       side_effect=AssertionError("不应依赖已连接会话")), \
     mock.patch.object(B, "config", _CFG), \
     mock.patch.object(B, "find_window_by_title", return_value=0):
    _r = _broker().restart_ths()
check("G6d 启动成功但等就绪超时 -> ok=False 且如实报错",
      (not _r["ok"]) and "超时" in _r["message"], _r)

# e) 完整重拉失败 -> 明确报错（main_f12 始终走 force_restart_main=True 完整重拉）
#    注意：离线测试必须把 _launch_main 打桩，否则会真的拉起同花顺（2026-09-13 踩过）。
_deep = []
with mock.patch.object(B, "_pids_of_xiadan", return_value=[]), \
     mock.patch.object(B.ThsBroker, "_launch_main",
                       lambda self, force_restart_main=False: (
                           _deep.append(force_restart_main), False)[1]), \
     mock.patch.object(B, "config", _CFG), \
     mock.patch.object(B.ThsBroker, "close_captcha_dialog",
                       side_effect=AssertionError("不应依赖已连接会话")):
    _r = _broker().restart_ths()
check("G6e 完整重拉失败 -> ok=False 且报『启动同花顺失败』",
      (not _r["ok"]) and "启动同花顺失败" in _r["message"], _r)
check("G6e2 main_f12 下始终以深度恢复方式（force_restart_main=True）完整重拉",
      _deep == [True], _deep)

# f) standalone 模式：不做深度恢复，直接报路径错误
_CFG_S = types.SimpleNamespace(THS_AUTOSTART_TIMEOUT=0, THS_RESTART_WAIT=1,
                               THS_LAUNCH_MODE="standalone",
                               THS_MAIN_PATH=r"D:\同花顺软件\同花顺\hexin.exe",
                               THS_MAIN_WAIT=1, THS_F12_RETRY=1)
_deep = []
with mock.patch.object(B, "_pids_of_xiadan", return_value=[]), \
     mock.patch.object(B.ThsBroker, "_launch_standalone", return_value=False), \
     mock.patch.object(B.ThsBroker, "_launch_main",
                       lambda self, force_restart_main=False: (
                           _deep.append(force_restart_main), False)[1]):
    with mock.patch.object(B, "config", _CFG_S), \
         mock.patch.object(B.ThsBroker, "close_captcha_dialog",
                           side_effect=AssertionError("不应依赖已连接会话")):
        _r = _broker().restart_ths()
check("G6f standalone 启动失败 -> 不深度恢复、直接报 xiadan 路径",
      (not _r["ok"]) and _deep == [] and "xiadan.exe" in _r["message"], _r)

# ==================== 汇总 ====================

print("\n" + "=" * 60)
print("结果：%d 通过 / %d 失败" % (len(PASS), len(FAIL)))
if FAIL:
    for _n in FAIL:
        print("  失败: " + _n)
print("=" * 60)
sys.exit(1 if FAIL else 0)
