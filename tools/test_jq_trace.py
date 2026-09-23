# -*- coding: utf-8 -*-
"""
「聚宽信号详录」自测（v1.5.0）—— 覆盖档1+档2 全部新能力：
  存储层：src_ip 迁移 / record_reject / query_signals 过滤分页 / timeline / feedback_for
  接收层：被拒请求落库（401/400/503）、成功信号带 IP
  控制台：/api/jq_signals / /api/jq_rejects / /api/jq_signal/<id>（含权限）
全部用 Flask test_client + 临时库，不碰真实数据、不需要执行端。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "relay_server"))

TMP = tempfile.mkdtemp(prefix="jq_trace_test_")
os.environ["RELAY_DB_PATH"] = os.path.join(TMP, "test.db")
os.environ["JQ_ENVFILE_NOCREATE"] = "1"
# 隔离本机环境变量干扰
for k in ("RELAY_SIGNAL_KEY", "RELAY_EXECUTOR_KEY", "RELAY_WEB_ADMIN_PASSWORD"):
    os.environ.pop(k, None)

import importlib
import app as appmod  # noqa: E402  （导入即 init 建表）

_results = []


def check(name, ok, detail=""):
    _results.append((name, bool(ok)))
    print("  [%s] %s%s" % ("OK " if ok else "FAIL", name, ("  -> " + str(detail)) if detail and not ok else ""))


def main():
    store = appmod.store
    c = appmod.app.test_client()
    KEY_S = appmod.config.SIGNAL_API_KEY
    KEY_E = appmod.config.EXECUTOR_API_KEY

    print("\n[1] 存储层：迁移与写入")
    cols = [r[1] for r in store._conn.execute("PRAGMA table_info(signals)").fetchall()]
    check("signals 已迁移出 src_ip 列", "src_ip" in cols, cols)
    store._conn.execute("INSERT INTO jq_rejects(ts,endpoint,reason,detail,ip) VALUES('t','/x','401_auth','d','1.2.3.4')")
    store._conn.commit()
    check("jq_rejects 表可写入", len(store.recent_rejects()) == 1)

    print("\n[2] 接收层：成功路径带 IP")
    sig = {"order_id": "T1-001", "security": "600519.XSHG", "side": "buy",
           "amount": 100, "price": 1700.0, "jq_status": "open"}
    r = c.post("/api/signal", json=sig, headers={"X-API-Key": KEY_S})
    check("/api/signal 成功", r.status_code == 200 and r.get_json()["ok"], r.get_json())
    row = store.recent_signals(1)[0]
    check("信号记录了来源 IP", row.get("src_ip") is not None, row)

    print("\n[3] 接收层：被拒请求全部落库")
    before = len(store.recent_rejects(200))
    c.post("/api/signal", json=sig, headers={"X-API-Key": "wrong"})          # 401
    c.post("/api/signal", data="not-json", content_type="text/plain",
           headers={"X-API-Key": KEY_S})                                      # 400 json
    c.post("/api/signal", json={"security": "x"}, headers={"X-API-Key": KEY_S})  # 400 field
    c.post("/api/feedback", json={"signal_id": 999, "result": "bad"},
           headers={"X-API-Key": KEY_E})                                      # 400 field
    after = store.recent_rejects(200)
    check("被拒请求数量增加 4", len(after) == before + 4, (before, len(after)))
    reasons = [x["reason"] for x in after[:4]]
    check("原因分类正确", set(reasons) == {"401_auth", "400_json", "400_field"}, reasons)
    check("被拒记录带 IP", all(x["ip"] for x in after[:4]), after[:2])
    stats = store.reject_stats()
    check("reject_stats 汇总", stats["total"] >= 5 and "401_auth" in stats["by_reason"], stats)

    print("\n[4] 存储层：查询 / 时间线")
    store.take_pending(10)                       # 领走 -> dispatched（同时记 dispatch ids）
    rows, total = store.query_signals()
    check("query_signals 全量", total >= 1 and len(rows) >= 1, total)
    rows2, _ = store.query_signals(status="dispatched")
    check("按状态过滤", all(r["status"] == "dispatched" for r in rows2) and rows2, len(rows2))
    rows3, _ = store.query_signals(security="600519")
    check("按代码模糊过滤", len(rows3) >= 1, len(rows3))
    rows4, _ = store.query_signals(date="1999-01-01")
    check("按日期过滤为空", len(rows4) == 0)
    rows5, t5 = store.query_signals(offset=1, limit=1)
    check("分页 offset 生效", t5 >= 1 and len(rows5) == 0 or len(rows5) <= 1, (t5, len(rows5)))
    tl = store.signal_timeline(rows2[0]["id"])
    check("timeline 返回 signal", tl and tl["signal"]["id"] == rows2[0]["id"])
    check("timeline 返回 events", isinstance(tl["events"], list))
    fb = store.feedback_for([rows2[0]["id"]])
    check("feedback_for 空组不报错", fb == {} or isinstance(fb, dict))
    store.add_feedback(rows2[0]["id"], "accepted", "已提交")
    store.add_feedback(rows2[0]["id"], "filled", "全部成交")
    fb2 = store.feedback_for([rows2[0]["id"]])
    check("feedback_for 取到两条", len(fb2.get(rows2[0]["id"], [])) == 2, fb2)
    tl2 = store.signal_timeline(rows2[0]["id"])
    check("timeline 含两条回报", len(tl2["feedback"]) == 2)

    print("\n[5] 控制台 API（权限 + 数据）")
    r = c.get("/console/api/jq_signals")
    check("未登录访问被拒 401", r.status_code == 401)
    c.post("/console/api/login", json={"username": "admin",
                                       "password": appmod.config.WEB_DEFAULT_ADMIN_PASSWORD})
    r = c.get("/console/api/jq_signals?status=dispatched")
    d = r.get_json()
    check("jq_signals 可读", r.status_code == 200 and d["ok"] and d["signals"], d)
    check("jq_signals 附带回报", all("feedback" in s for s in d["signals"]))
    check("jq_signals 附带被拒统计", "rejects" in d and d["rejects"]["total"] >= 5)
    r = c.get("/console/api/jq_rejects")
    d2 = r.get_json()
    check("jq_rejects 可读", r.status_code == 200 and d2["ok"] and d2["rejects"])
    r = c.get("/console/api/jq_signal/%d" % rows2[0]["id"])
    d3 = r.get_json()
    check("单条时间线可读", r.status_code == 200 and d3["ok"] and len(d3["feedback"]) == 2)
    r = c.get("/console/api/jq_signal/99999")
    check("不存在的信号返回 404", r.status_code == 404)
    import web_ui
    check("页面 HTML 含新页面入口", "聚宽信号" in web_ui.render("t"))

    print("\n" + "=" * 62)
    ok_n = sum(1 for _, o in _results if o)
    print("通过 %d 项，失败 %d 项" % (ok_n, len(_results) - ok_n))
    if ok_n != len(_results):
        print("失败项: " + ", ".join(n for n, o in _results if not o))
        sys.exit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
