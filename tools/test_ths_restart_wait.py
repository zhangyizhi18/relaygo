# -*- coding: utf-8 -*-
"""重启同花顺「等待就绪」逻辑测试（不依赖真机）。

验证 ThsBroker.wait_until_ready：
  - connect 失败 / health_check 未通过时持续重试，直到成功 -> (True, msg)
  - connect 抛异常（窗口未出现/无窗口）也重试
  - 始终不就绪 -> 超时返回 (False, 最后错误)

用「假时钟」替换 broker_ths 模块内的 time，使等待循环确定性、瞬时完成，
且不污染全局 time 模块（上次误改全局 sleep 导致空转刷屏）。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "executor"))

import broker_ths as m   # noqa: E402


class FakeTime(object):
    """可控时钟：sleep 只推进虚拟时间，不真的等待。"""

    def __init__(self):
        self.t = 0.0

    def time(self):
        return self.t

    def sleep(self, s):
        self.t += s


def make_broker(attempts, fake_time):
    """attempts: [(connect_exc_or_None, (health_ok, health_msg)), ...]
    最后一次会被重复使用（模拟持续同一状态）。"""
    b = object.__new__(m.ThsBroker)
    st = {"n": 0, "cur": 0}

    def fake_connect(*args, **kwargs):
        # 注意：wait_until_ready 会以 connect(verbose=False) 调用（降噪），
        # 桩必须能接受关键字参数，否则会被当成"连接异常"而永远等不到就绪。
        st["cur"] = min(st["n"], len(attempts) - 1)
        st["n"] += 1
        exc = attempts[st["cur"]][0]
        if exc:
            raise exc
        return None

    def fake_health():
        return attempts[st["cur"]][1]

    b.connect = fake_connect
    b.health_check = fake_health
    m.time = fake_time
    return b


PASS, FAIL = [], []


def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name)


# T1: 前 2 次未就绪（模拟重启后自动登录需要时间），第 3 次成功
ft = FakeTime()
b = make_broker([(None, (False, "未登录")),
                 (None, (False, "未登录")),
                 (None, (True, "连接正常"))], ft)
ok, msg = b.wait_until_ready(timeout=60)
check("T1 重试后成功 -> True", ok is True)
check("T1 带回成功消息", msg == "连接正常")
check("T1 只前进了必要的虚拟时间", ft.t == 10.0)

# T2: connect 抛异常（窗口未出现）也应重试，最终成功
ft2 = FakeTime()
b2 = make_broker([(RuntimeError("No windows for that process could be found"), (False, "")),
                  (None, (True, "ok"))], ft2)
ok2, _ = b2.wait_until_ready(timeout=60)
check("T2 connect 异常后重试成功 -> True", ok2 is True)

# T3: 始终不就绪 -> 超时 False，并带回最后错误
ft3 = FakeTime()
b3 = make_broker([(None, (False, "一直没有登录"))], ft3)
ok3, msg3 = b3.wait_until_ready(timeout=6)
check("T3 超时 -> False", ok3 is False)
check("T3 带回最后错误", "登录" in msg3)

# T4: 源码级断言——restart_ths 已改为等待就绪，且不再单次 connect 就报错
src = open(os.path.join(ROOT, "executor", "broker_ths.py"), encoding="utf-8").read()
check("T4 restart_ths 使用 wait_until_ready",
      "self.wait_until_ready(timeout=config.THS_RESTART_WAIT)" in src)
check("T4 旧的单次重连报错文案已移除", "已重启程序但重新连接失败" not in src)

print("\n== %d passed, %d failed ==" % (len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
