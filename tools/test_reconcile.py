# -*- coding: utf-8 -*-
"""
「盘后自动对账」自测（v1.7.0）—— 覆盖：
  存储层：jq_positions / reconcile 两张新表
  接收层：/api/jq_positions（401/400/成功、脏数据过滤、整表替换）
  比对层：_compare 纯函数（一致/差额/单边缺失）
  控制台：/api/reconcile / /api/reconcile/run（含权限、执行端离线分支）
全部用 Flask test_client + 临时库，不碰真实数据、不需要执行端。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "relay_server"))

TMP = tempfile.mkdtemp(prefix="jq_reconcile_test_")
os.environ["RELAY_DB_PATH"] = os.path.join(TMP, "test.db")
os.environ["JQ_ENVFILE_NOCREATE"] = "1"
for k in ("RELAY_SIGNAL_KEY", "RELAY_EXECUTOR_KEY", "RELAY_WEB_ADMIN_PASSWORD"):
    os.environ.pop(k, None)

import app as appmod  # noqa: E402  （导入即 init 建表 + 起 auto 线程）

_results = []


def check(name, ok, detail=""):
    _results.append((name, bool(ok)))
    print("  [%s] %s%s" % ("OK " if ok else "FAIL", name,
                           ("  -> " + str(detail)) if detail and not ok else ""))


def login(c, username, password):
    r = c.post("/console/api/login", json={"username": username, "password": password})
    return r


def main():
    sw = appmod.store_web
    rec = appmod.reconcile
    c = appmod.app.test_client()
    KEY_S = appmod.config.SIGNAL_API_KEY

    print("\n[1] 存储层：新表")
    check("jq_positions 可写可读", (sw.save_jq_positions(
        [{"code": "600519.XSHG", "name": "贵州茅台", "amount": 100}]) or True)
        and sw.get_jq_positions()[0]["code"] == "600519")
    sw.save_jq_positions([{"code": "510300.XSHG", "name": "沪深300ETF", "amount": 200}])
    check("快照整表替换", len(sw.get_jq_positions()) == 1
          and sw.get_jq_positions()[0]["code"] == "510300")
    sw.save_reconcile("t", "ok", {"summary": "x", "items": []})
    check("reconcile 可写，last 可读", sw.last_reconcile()["status"] == "ok")

    print("\n[2] 比对层：_compare 纯函数")
    st, items = rec._compare({"600519": 100}, {"600519": 100})
    check("完全一致 -> ok", st == "ok" and len(items) == 1)
    st, items = rec._compare({"600519": 100}, {"600519": 60})
    check("数量差 -> mismatch", st == "mismatch" and items[0]["diff"] == -40)
    st, items = rec._compare({"600519": 100}, {"000001": 500})
    check("单边缺失 -> mismatch 两条", st == "mismatch" and len(items) == 2
          and {i["note"] for i in items} == {"聚宽快照中无此持仓", "券商持仓中无此代码"})
    st, _ = rec._compare({}, {})
    check("双方都空 -> ok", st == "ok")
    check("plain_code 三种格式归一",
          (rec.plain_code("600519.XSHG"), rec.plain_code("600519.SH"),
           rec.plain_code("600519")) == ("600519",) * 3)
    check("parse_broker_positions 认 THS 列名",
          rec.parse_broker_positions([{"证券代码": "510300", "证券名称": "沪深300ETF",
                                       "持仓数量": "100"}]) == {"510300": {"name": "沪深300ETF", "amount": 100}})

    print("\n[3] 接收层：/api/jq_positions")
    r = c.post("/api/jq_positions", json={"positions": []}, headers={"X-API-Key": "wrong"})
    check("错误密钥被拒(401)", r.status_code == 401)
    r = c.post("/api/jq_positions", data="bad", content_type="text/plain",
               headers={"X-API-Key": KEY_S})
    check("非 JSON 被拒(400)", r.status_code == 400)
    r = c.post("/api/jq_positions", json={"no": 1}, headers={"X-API-Key": KEY_S})
    check("缺 positions 被拒(400)", r.status_code == 400)
    r = c.post("/api/jq_positions", json={"positions": [
        {"security": "600519.XSHG", "name": "贵州茅台", "total_amount": 100},
        {"security": "000001.XSHE", "total_amount": 1000},
        {"security": "BAD", "total_amount": 5},          # 无数字代码 -> 过滤
        {"security": "510300.XSHG", "total_amount": 0},  # 0 股 -> 过滤
    ]}, headers={"X-API-Key": KEY_S})
    d = r.get_json()
    check("上报成功且脏数据被过滤", r.status_code == 200 and d["ok"] and d["saved"] == 2, d)
    snap = {x["code"]: x["amount"] for x in sw.get_jq_positions()}
    check("快照内容正确", snap == {"600519": 100, "000001": 1000}, snap)

    print("\n[4] 控制台：对账接口与权限")
    login(c, "admin", "admin123")
    r = c.get("/console/api/reconcile")
    d = r.get_json()
    check("查看最近对账", r.status_code == 200 and d["ok"] and d["jq_count"] == 2
          and d["auto_enabled"] and d["auto_at"], d)
    r = c.post("/console/api/reconcile/run", json={})
    d = r.get_json()
    check("立即对账（执行端离线分支）", r.status_code == 200 and d["ok"]
          and d["result"]["status"] == "executor_offline", d)
    check("离线结果已落库", sw.last_reconcile()["status"] == "executor_offline")
    sw.save_jq_positions([])   # 清空快照验证 no_snapshot 分支
    r = c.post("/console/api/reconcile/run", json={})
    check("无快照分支 no_snapshot", r.get_json()["result"]["status"] == "no_snapshot")
    sw.save_jq_positions([{"code": "600519", "amount": 100}])

    print("\n[5] 权限：viewer 不能触发对账")
    c.post("/console/api/users", json={"username": "t_view", "password": "pass123456",
                                       "role": "viewer"})
    c.get("/console/api/logout")
    login(c, "t_view", "pass123456")
    r = c.post("/console/api/reconcile/run", json={})
    check("viewer 触发被拒(403)", r.status_code == 403)
    r = c.get("/console/api/reconcile")
    check("viewer 可查看对账结果", r.status_code == 200)

    print("\n[6] 页面特征")
    c.get("/console/api/logout")
    login(c, "admin", "admin123")
    r = c.get("/console/")
    check("页面含对账卡片", "持仓对账" in r.get_data(as_text=True))

    failed = [n for n, ok in _results if not ok]
    print("\n================================================\n"
          "结果：通过 %d 项，失败 %d 项\n%s" % (
              len(_results) - len(failed), len(failed),
              "失败项: " + ", ".join(failed) if failed else "全部通过 ✓"))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
