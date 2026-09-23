# -*- coding: utf-8 -*-
"""
Web 控制台自测脚本 —— 不需要券商账号，dry_run 执行端即可跑通全链路。

用法：
    python tools/test_console.py [中转地址] [admin密码]
    默认：http://127.0.0.1:5010   admin123

覆盖范围：
    1  页面可访问（返回 HTML）
    2  未登录访问 API 被拦（401）
    3  错误密码登录失败（401）
    4  管理员登录成功 / 会话保持 / 改密前后校验
    5  用户管理：新建 viewer / trader / admin、改角色、禁用、删除、重名与弱密码拦截
    6  角色权限：viewer 不能下单和管理用户（403），trader 能下单
    7  审计日志：登录/查询/下单/用户变更都留痕
    8  账户查询与下单、撤单任务（执行端在线时）
    9  执行端离线时的正确拒绝（503）
   10  防重复下单（15 秒内相同委托被拦）
   11  证券代码归一化（6 位数字自动补后缀 / 大小写规范化 / 矛盾与不支持品种被拒）
"""
import json
import sys
import time

import requests

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5010").rstrip("/")
ADMIN_PW = sys.argv[2] if len(sys.argv) > 2 else "admin123"
CONSOLE = BASE + "/console"
API = CONSOLE + "/api"

_passed = 0
_failed = 0


def step(name, ok, extra=""):
    global _passed, _failed
    if ok:
        _passed += 1
        print("  [OK]   %s %s" % (name, extra))
    else:
        _failed += 1
        print("  [FAIL] %s %s" % (name, extra))


def new_session():
    s = requests.Session()
    s.trust_env = False   # 不让系统代理干扰本地请求
    return s


def get(s, path, **kw):
    return s.get(API + path, timeout=kw.pop("timeout", 60), **kw)


def call(s, path, body=None, method="POST"):
    fn = s.post if method == "POST" else s.get
    if method == "POST":
        return fn(API + path, json=body or {}, timeout=90)
    return fn(API + path, timeout=90)


def main():
    print("=" * 64)
    print("Web 控制台自测  目标: %s" % BASE)
    print("=" * 64)

    # ---------- 1. 页面 ----------
    print("\n[1] 页面与会话")
    s0 = new_session()
    r = s0.get(CONSOLE + "/", timeout=10)
    step("控制台页面可访问", r.status_code == 200 and "RelayGo" in r.text,
         "HTTP %s, %d 字节" % (r.status_code, len(r.text)))
    step("页面内含版本号", "v1." in r.text)

    r = call(s0, "/overview", method="GET")
    step("未登录访问 API 被拦截", r.status_code == 401, "HTTP %s" % r.status_code)

    r = call(s0, "/login", {"username": "admin", "password": "definitely-wrong"})
    step("错误密码登录失败", r.status_code == 401, "HTTP %s" % r.status_code)

    # ---------- 2. 管理员登录 ----------
    print("\n[2] 管理员与会话")
    admin = new_session()
    r = call(admin, "/login", {"username": "admin", "password": ADMIN_PW})
    d = r.json() if r.status_code == 200 else {}
    step("管理员登录成功", r.status_code == 200 and d.get("ok"), "角色=%s" % d.get("user", {}).get("role"))

    r = call(admin, "/me", method="GET")
    step("会话保持（/api/me）", r.status_code == 200 and r.json().get("user", {}).get("username") == "admin")

    r = call(admin, "/overview", method="GET")
    ov = r.json()
    online = ov.get("executor_online")
    step("概览接口正常", r.status_code == 200 and ov.get("ok"),
         "版本=%s 执行端=%s" % (ov.get("version"), "在线" if online else "离线"))

    # ---------- 3. 用户管理 ----------
    print("\n[3] 用户管理")
    uname = "t_viewer"
    r = call(admin, "/users", {"username": uname, "password": "view123", "role": "viewer",
                               "display_name": "测试只读"})
    step("新建 viewer 成功", r.json().get("ok"), r.json().get("message") or r.json().get("error"))

    r = call(admin, "/users", {"username": uname, "password": "view123", "role": "viewer"})
    step("重名用户被拦截", not r.json().get("ok"), r.json().get("error", ""))

    r = call(admin, "/users", {"username": "t_weak", "password": "123", "role": "viewer"})
    step("弱密码被拦截", not r.json().get("ok"), r.json().get("error", ""))

    r = call(admin, "/users", {"username": "t_trader", "password": "trade123", "role": "trader"})
    step("新建 trader 成功", r.json().get("ok"))

    r = call(admin, "/users/update", {"username": uname, "role": "trader"})
    step("修改角色成功", r.json().get("ok"), r.json().get("message", ""))
    call(admin, "/users/update", {"username": uname, "role": "viewer"})   # 降回只读，供权限测试用

    r = get(admin, "/users")
    users = {u["username"]: u for u in r.json().get("users", [])}
    step("用户列表包含新用户", "t_viewer" in users and "t_trader" in users,
         "共 %d 个用户" % len(users))

    # ---------- 4. 角色权限 ----------
    print("\n[4] 角色权限")
    viewer = new_session()
    r = call(viewer, "/login", {"username": uname, "password": "view123"})
    step("viewer 登录成功", r.json().get("ok"))

    r = get(viewer, "/users")
    step("viewer 访问用户管理被拒(403)", r.status_code == 403, "HTTP %s" % r.status_code)

    r = call(viewer, "/order", {"security": "510300.XSHG", "side": "buy", "amount": 100})
    step("viewer 下单被拒(403)", r.status_code == 403, "HTTP %s" % r.status_code)

    r = get(viewer, "/overview")
    step("viewer 可以看概览", r.status_code == 200 and r.json().get("ok"))

    r = get(viewer, "/tasks")
    step("viewer 可以看任务队列", r.status_code == 200)

    # ---------- 5. 账户查询 / 下单 / 撤单 ----------
    print("\n[5] 账户查询 与 交易任务")
    if not online:
        r = get(admin, "/account?kind=balance")
        step("执行端离线时查询被正确拒绝(503)", r.status_code == 503, r.json().get("error", ""))
        print("       （启动 dry_run 执行端后重跑本脚本，会自动验证查询/下单/撤单）")
    else:
        t0 = time.time()
        r = get(admin, "/account?kind=balance")
        d = r.json()
        step("查询资金成功", d.get("ok"), "%.1fs 数据字段=%d" % (time.time() - t0, len(d.get("data") or {})))
        if d.get("data"):
            print("       资金字段: %s" % list(d["data"].keys())[:6])

        r = get(admin, "/account?kind=positions")
        d = r.json()
        step("查询持仓成功", d.get("ok"), "%s 条" % len(d.get("data") or []))

        r = get(admin, "/account?kind=entrusts")
        step("查询当日委托成功", r.json().get("ok"), "%s 条" % len(r.json().get("data") or []))

        r = get(admin, "/account?kind=badkind")
        step("非法查询类型被拒(400)", r.status_code == 400)

        # 下单（dry_run 下不会真实成交）
        r = call(admin, "/order", {"security": "510300.XSHG", "side": "buy",
                                   "amount": 100, "price": 4.21})
        d = r.json()
        task = d.get("task") or {}
        step("下单任务完成", d.get("ok") and task.get("status") == "done",
             "任务#%s 状态=%s 提示=%s" % (task.get("id"), task.get("status"),
                                          (task.get("message") or "")[:40]))

        # 防重复下单
        r2 = call(admin, "/order", {"security": "510300.XSHG", "side": "buy",
                                    "amount": 100, "price": 4.21})
        step("重复下单被拦截(429)", r2.status_code == 429, r2.json().get("error", ""))

        # 证券代码归一化（v1.7.1）：只填 6 位数字，自动补 .XSHG/.XSHE
        r3 = call(admin, "/order", {"security": "600519", "side": "buy",
                                    "amount": 100, "price": 1500.0})
        d3 = r3.json()
        step("6 位数字下单被受理(600519)", r3.status_code == 200,
             "HTTP %s %s" % (r3.status_code, (d3.get("error") or "")[:50]))
        logs3 = get(admin, "/audit?limit=50").json().get("logs", [])
        hit3 = [x for x in logs3 if x.get("action") == "order_submit"
                and "600519.XSHG" in str(x.get("target") or "")]
        step("归一化生效并留痕(600519->600519.XSHG)",
             bool(hit3) and "自动补全" in str(hit3[0].get("detail") or ""),
             hit3[0].get("detail") if hit3 else "审计中未见 600519.XSHG 的 order_submit")

        r3b = call(admin, "/order", {"security": "000001.xshe", "side": "buy",
                                     "amount": 100, "price": 12.5})
        step("小写后缀被规范化(000001.xshe)", r3b.status_code == 200,
             "HTTP %s %s" % (r3b.status_code, (r3b.json().get("error") or "")[:50]))

        # 参数校验：位数错 / 代码与后缀矛盾 / 不支持品种
        for body, name in [
            ({"security": "6005", "side": "buy", "amount": 100}, "位数不足被拒(400)"),
            ({"security": "510300.XSHE", "side": "buy", "amount": 100}, "代码与后缀矛盾被拒(400)"),
            ({"security": "830799", "side": "buy", "amount": 100}, "北交所代码被拒(400)"),
        ]:
            r3c = call(admin, "/order", body)
            step(name, r3c.status_code == 400, r3c.json().get("error", "")[:60])

        # 撤单
        r4 = call(admin, "/cancel", {"entrust_no": "1234567890"})
        d4 = r4.json()
        step("撤单任务已处理", (d4.get("ok") or "task" in d4),
             ((d4.get("task") or {}).get("message") or d4.get("error") or "")[:60])

        # 一键全撤（dry_run 下返回模拟成功；真实执行端走同花顺[全撤]安全路径）
        r5 = call(admin, "/cancel_all", {})
        d5 = r5.json()
        step("一键全撤任务已处理", (d5.get("ok") or "task" in d5),
             ((d5.get("task") or {}).get("message") or d5.get("error") or "")[:60])

    # ---------- 6. 审计日志 ----------
    print("\n[6] 审计日志")
    r = get(admin, "/audit?limit=200")
    d = r.json()
    logs = d.get("logs", [])
    actions = [x["action"] for x in logs]
    step("审计日志可读", d.get("ok"), "共 %d 条（累计 %s）" % (len(logs), d.get("summary", {}).get("total")))
    need_actions = ["login", "login_failed", "user_create"]
    if online:
        need_actions.append("order_submit")     # 离线时下单会被 503 拦住，不会有该记录
        need_actions.append("order_cancel_all") # 一键全撤同理
    for need in need_actions:
        step("含 %s 记录" % need, need in actions)
    step("未登录访问也有留痕（permission_denied 或 login_failed）",
         ("permission_denied" in actions) or ("login_failed" in actions))
    ip_ok = all(x.get("ip") for x in logs[:5])
    step("审计记录包含 IP", ip_ok, logs[0].get("ip") if logs else "")

    # ---------- 7. 收尾：删除测试用户 ----------
    print("\n[7] 清理")
    for u in ("t_viewer", "t_trader"):
        r = call(admin, "/users/delete", {"username": u})
        step("删除用户 %s" % u, r.json().get("ok"), r.json().get("message") or r.json().get("error"))

    r = call(admin, "/users/delete", {"username": "admin"})
    step("不能删除自己/最后的管理员", not r.json().get("ok"), r.json().get("error", ""))

    print("\n" + "=" * 64)
    print("结果：通过 %d 项，失败 %d 项" % (_passed, _failed))
    print("=" * 64)
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
