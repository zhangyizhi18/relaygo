# -*- coding: utf-8 -*-
"""`ThsBroker._verify_submitted` 的离线回归单测（不碰 GUI / 同花顺 / 网络）。

对应 2026-09-13 真实三轮下单测试暴露的 **假成功**：
    第 2 轮 510500 实际没提交成功（当日委托里查无此单、全撤只撤到 9 笔），
    执行端却报"下单成功"——因为提交时弹出验证码，走了"弹窗后补证"分支，
    而 _verify_submitted 只按 代码+方向+价格+数量 匹配当日委托，返回的
    合同编号 6254583893 其实是 **第 1 轮那笔已撤的 510500**（同花顺当日
    委托会保留已撤记录，同代码同价同量的多轮单会永久匹配到它）。

修复：_verify_submitted 增加两道过滤
  1) 跳过已撤单记录（备注含"撤" 或 撤消数量>0）；
  2) 委托时间必须 ≥ since（本次下单时刻，带回溯余量）。
  委托时间解析不出来时保守判为"未提交"（宁可让人工核对，也不误报成功）。

用法：venv/Scripts/python.exe tools/test_ths_verify_submitted.py
"""
import datetime
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "executor"))
os.environ.setdefault("JQ_ENVFILE_NOCREATE", "1")   # 别因自测去写 .env

import broker_ths as B      # noqa: E402

PASS, FAIL = [], []


def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name)


def hhmmss(delta_seconds):
    """当前时间往前 delta_seconds 的 HH:MM:SS 字符串（同花顺委托时间格式）。"""
    return (datetime.datetime.now() - datetime.timedelta(seconds=delta_seconds)
            ).strftime("%H:%M:%S")


def row(code="510500", op="买入", price=7.534, amt=100, no="", ts="",
        remark="未成交", cancel=0):
    return {"证券代码": code, "操作": op, "委托价格": price, "委托数量": amt,
            "合同编号": no, "委托时间": ts, "备注": remark, "撤消数量": cancel}


def broker_with(rows):
    """造一个只替换掉 query_entrusts 的 ThsBroker（不 connect、不发网络请求）。"""
    b = B.ThsBroker("dummy-xiadan.exe")
    b.query_entrusts = lambda: list(rows)
    return b


OLD = hhmmss(600)          # 10 分钟前的委托（当天历史）
FRESH = hhmmss(5)          # 5 秒前，属于"本次刚提交"

print("== 0. 辅助函数 ==")
check("_is_cancelled_row: 备注含撤 -> True",
      B.ThsBroker._is_cancelled_row(row(remark="全部撤单")) is True)
check("_is_cancelled_row: 撤消数量>0 -> True",
      B.ThsBroker._is_cancelled_row(row(cancel=100)) is True)
check("_is_cancelled_row: 普通未成交 -> False",
      B.ThsBroker._is_cancelled_row(row()) is False)
ts = B.ThsBroker._entrust_time_ts(row(ts="18:24:55"))
check("_entrust_time_ts: 解析 HH:MM:SS 并补今天日期",
      ts is not None and datetime.date.fromtimestamp(ts) == datetime.date.today())
check("_entrust_time_ts: 空串 -> None", B.ThsBroker._entrust_time_ts(row(ts="")) is None)
check("_entrust_time_ts: 垃圾串 -> None",
      B.ThsBroker._entrust_time_ts(row(ts="--")) is None)

print("== 1. 核心回归：已撤单不得当作本次提交成功 ==")
b = broker_with([row(no="OLD_CANCELLED", ts=OLD, remark="全部撤单", cancel=100)])
check("只有[已撤的老单]时 -> 判定未提交（修复前会误报成功 OLD_CANCELLED）",
      b._verify_submitted("510500", 7.534, 100, "buy",
                          timeout=0.01, since=datetime.datetime.now().timestamp() - 120) == "")
check("同样输入、把 since 放宽到 2 小时前 -> 仍为空（撤单过滤独立生效）",
      b._verify_submitted("510500", 7.534, 100, "buy",
                          timeout=0.01, since=datetime.datetime.now().timestamp() - 7200) == "")

print("== 2. 老而未撤的单也不认（时间过滤） ==")
b = broker_with([row(no="OLD_LIVE", ts=OLD, remark="未成交", cancel=0)])
check("只有[10分钟前的活单]时 -> 判定未提交",
      b._verify_submitted("510500", 7.534, 100, "buy",
                          timeout=0.01, since=datetime.datetime.now().timestamp() - 120) == "")

print("== 3. 本次刚提交的活单要能认出来（正向） ==")
b = broker_with([row(no="OLD_CANCELLED", ts=OLD, remark="全部撤单", cancel=100),
                 row(no="NEW_OK", ts=FRESH, remark="未成交", cancel=0)])
check("老撤单 + 本次新单 -> 返回 NEW_OK（而不是老撤单编号）",
      b._verify_submitted("510500", 7.534, 100, "buy",
                          timeout=0.01,
                          since=datetime.datetime.now().timestamp() - 120) == "NEW_OK")

print("== 4. 本次刚提交但立刻被撤 -> 不认 ==")
b = broker_with([row(no="NEW_BUT_CANCELLED", ts=FRESH, remark="全部撤单", cancel=100)])
check("本次新单但已撤 -> 判定未提交",
      b._verify_submitted("510500", 7.534, 100, "buy",
                          timeout=0.01,
                          since=datetime.datetime.now().timestamp() - 120) == "")

print("== 5. 委托时间缺失 -> 保守判未提交 ==")
b = broker_with([row(no="NO_TIME", ts="", remark="未成交", cancel=0)])
check("无委托时间的新单 -> 判定未提交（不误报成功）",
      b._verify_submitted("510500", 7.534, 100, "buy",
                          timeout=0.01,
                          since=datetime.datetime.now().timestamp() - 120) == "")

print("== 6. 字段匹配仍然严格（不匹配的不认） ==")
b = broker_with([row(code="510500", no="X", ts=FRESH)])
check("价格不符 -> 未匹配",
      b._verify_submitted("510500", 7.999, 100, "buy",
                          timeout=0.01,
                          since=datetime.datetime.now().timestamp() - 120) == "")
check("数量不符 -> 未匹配",
      b._verify_submitted("510500", 7.534, 200, "buy",
                          timeout=0.01,
                          since=datetime.datetime.now().timestamp() - 120) == "")
check("方向不符(买/卖) -> 未匹配",
      b._verify_submitted("510500", 7.534, 100, "sell",
                          timeout=0.01,
                          since=datetime.datetime.now().timestamp() - 120) == "")
b = broker_with([row(code="159915", no="Y", ts=FRESH)])
check("代码不符 -> 未匹配",
      b._verify_submitted("510500", 7.534, 100, "buy",
                          timeout=0.01,
                          since=datetime.datetime.now().timestamp() - 120) == "")

print("== 7. since 默认值（不传）也能拦住当天老单 ==")
b = broker_with([row(no="OLD_CANCELLED", ts=OLD, remark="全部撤单", cancel=100)])
check("不传 since -> 老撤单仍不认",
      b._verify_submitted("510500", 7.534, 100, "buy", timeout=0.01) == "")

print()
print("=" * 60)
print("结果：PASS %d / FAIL %d（共 %d）" % (len(PASS), len(FAIL), len(PASS) + len(FAIL)))
if FAIL:
    for n in FAIL:
        print("  FAIL:", n)
print("=" * 60)
sys.exit(0 if not FAIL else 1)
