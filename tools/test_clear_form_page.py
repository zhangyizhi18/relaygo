# -*- coding: utf-8 -*-
"""清场（clear_form）「先切页再点[重填]」修复测试。

背景（2026-09-12 真机日志）：执行端连上同花顺后，控制台点「清场」报
    任务 #124 完成 ok=False 未找到[重填]按钮(1007)，请确认当前处于下单页
根因：[重填](1007) 只存在于买入/卖出页；同花顺停在查询/其它页时该控件根本不
存在。原实现直接找按钮 → 必然失败。

修复：clear_form 先 _switch_page("buy") 切到买入页 → 等渲染 → 最多找 3 次
[重填] → 万一没有（版本差异）退化为键盘清空 代码/价格/数量 三个输入框。

本测试分两部分：
  A. 离线（默认，必跑）：用假对象验证 clear_form 的分支逻辑与调用顺序，
     并做源码级断言（防回退）。
  B. 真机（--live，需同花顺已登录）：复现故障前置条件——先切到查询页、
     断言那时 1007 找不到；再调 clear_form，断言成功且切页后 1007 存在。

用法：
  python tools/test_clear_form_page.py            # 只跑 A（不碰同花顺）
  python tools/test_clear_form_page.py --live     # A + B（会切同花顺页面）
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "executor"))

import broker_ths as m   # noqa: E402

PASS, FAIL = [], []


def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name)


# ==================== A. 离线分支逻辑 ====================

class FakeButton(object):
    def __init__(self):
        self.clicked = 0

    def click_input(self):
        self.clicked += 1


def make_broker(find_results, keyboard_cleared=3):
    """find_results：每次 _find_button 的返回值序列（末尾值会一直被复用）。
    keyboard_cleared：_clear_inputs_by_keyboard 的返回值。"""
    b = object.__new__(m.ThsBroker)
    b.user = type("FakeUser", (), {"_main": object()})()   # 非 None 即可
    b.xiadan_path = ""
    calls = {"switch": [], "find": 0, "kb": 0}

    def fake_ensure_fg():
        return True

    def fake_switch(direction):
        calls["switch"].append(direction)
        return True

    def fake_find(control_id, text=None):
        i = min(calls["find"], len(find_results) - 1)
        calls["find"] += 1
        return find_results[i]

    def fake_kb():
        calls["kb"] += 1
        return keyboard_cleared

    b._ensure_fg = fake_ensure_fg
    b._switch_page = fake_switch
    b._find_button = fake_find
    b._clear_inputs_by_keyboard = fake_kb
    return b, calls


m.time = type("T", (), {"time": staticmethod(lambda: 0.0),
                        "sleep": staticmethod(lambda s: None)})()

# T1: 找到 [重填] -> 点它，且**先切到买入页**
btn = FakeButton()
b, calls = make_broker([btn])
r = b.clear_form()
check("T1 找到[重填] -> ok=True", r["ok"] is True)
check("T1 已真实点击按钮", btn.clicked == 1)
check("T1 切页动作为 buy 且早于找按钮", calls["switch"] == ["buy"])

# T2: 前两次找不到、第三次找到（切页后控件渲染慢的场景）
btn2 = FakeButton()
b2, calls2 = make_broker([None, None, btn2])
r2 = b2.clear_form()
check("T2 重试后找到 -> ok=True", r2["ok"] is True)
check("T2 共查找 3 次", calls2["find"] == 3)
check("T2 未触发键盘兜底", calls2["kb"] == 0)

# T3: 始终找不到 -> 键盘兜底清空
b3, calls3 = make_broker([None], keyboard_cleared=3)
r3 = b3.clear_form()
check("T3 找不到按钮时走键盘兜底 -> ok=True", r3["ok"] is True)
check("T3 提示键盘清空"  , "键盘清空" in r3["message"])
check("T3 兜底被调用一次", calls3["kb"] == 1)

# T4: 找不到按钮且键盘也清不掉 -> 如实报失败
b4, _ = make_broker([None], keyboard_cleared=0)
r4 = b4.clear_form()
check("T4 两条路都失败 -> ok=False", r4["ok"] is False)
check("T4 报错文案提到[重填]", "重填" in r4["message"])

# T5: 未连接 -> 明确报"未连接"，不做任何点击
b5 = object.__new__(m.ThsBroker)
b5.user = None
r5 = b5.clear_form()
check("T5 未连接 -> ok=False 且提示未连接", r5["ok"] is False and "未连接" in r5["message"])

# T6: 源码级断言（防回退）
src = open(os.path.join(ROOT, "executor", "broker_ths.py"), encoding="utf-8").read()
check("T6 clear_form 先切买入页", 'self._switch_page("buy")' in src)
check("T6 clear_form 有键盘兜底", "_clear_inputs_by_keyboard" in src)
check("T6 _find_button 带文本匹配", 'text="重填"' in src)
compat = open(os.path.join(ROOT, "executor", "pywinauto_compat.py"), encoding="utf-8").read()
check("T6 垫片提供 find_button 手工过滤",
      "def find_button(main, control_id, text=None)" in compat)
check("T6 下单前清表单也改用 find_button",
      'find_button(getattr(self, "_main", None),' in compat)
check("T6 不再用会静默忽略条件的 child_window(control_id=..) 找按钮",
      'child_window(control_id=REFILL_BTN_ID' not in compat)


# ==================== B. 真机验证（--live） ====================

def live():
    print("\n---- 真机验证（会切换同花顺页面，不会下单）----")
    import config
    b = m.ThsBroker(config.THS_XIADAN_PATH)
    b.connect()
    ok, msg = b.health_check()
    check("L1 连接成功且已登录", ok is True)
    if not ok:
        print("   连接异常，跳过后续真机用例: %s" % msg)
        return

    # 前置：切到查询页（复现故障现场：清场时不在下单页）
    try:
        b.user._switch_left_menus(["查询[F4]", "当日委托"])
    except Exception:
        b.user._switch_left_menus_by_shortcut("{F4}")
    import time
    time.sleep(1.2)
    before = b._find_button(1007, text="重填")
    print("   查询页下能否找到[重填](1007): %s （None=复现成功）"
          % ("找到" if before is not None else "None"))
    check("L2 复现故障前置条件：查询页没有[重填]", before is None)

    # 修复后的 clear_form：应自动切回买入页并点掉[重填]
    r = b.clear_form()
    print("   clear_form 返回: %s" % r)
    check("L3 查询页下清场成功", r["ok"] is True)
    after = b._find_button(1007, text="重填")
    check("L4 清场后确实回到了买入页（1007 在）", after is not None)
    check("L5 买入按钮(1006)也在，确认是下单页",
          b._find_button(1006) is not None)


if __name__ == "__main__":
    if "--live" in sys.argv:
        try:
            live()
        except Exception as e:
            check("LIVE 异常: %r" % str(e)[:120], False)
    print("\n== %d passed, %d failed ==" % (len(PASS), len(FAIL)))
    sys.exit(1 if FAIL else 0)
