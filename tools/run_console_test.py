# -*- coding: utf-8 -*-
"""
Web 控制台一键自测（不需要券商账号）。

做三件事：
  1. 用一个临时数据库启动中转服务（不动你的 relay_data.db）
  2. 可选：启动 dry_run 执行端（验证账户查询/下单/撤单任务链路）
  3. 调用 tools/test_console.py 跑完整自测

用法：
    python tools/run_console_test.py                  # 只测控制台本身（覆盖"执行端离线"分支）
    python tools/run_console_test.py --with-executor  # 连 dry_run 执行端一起测（推荐，36 项）
"""
import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
PORT = os.environ.get("TEST_RELAY_PORT", "5098")
DB = os.path.join(tempfile.gettempdir(), "jq_console_test.db")
WITH_EXEC = "--with-executor" in sys.argv

procs = []


def spawn(args, cwd, env_extra=None):
    env = os.environ.copy()
    env.update(env_extra or {})
    p = subprocess.Popen(args, cwd=cwd, env=env)
    procs.append(p)
    return p


def main():
    print(">> 启动中转服务（临时库，端口 %s）…" % PORT)
    spawn([PY, "app.py"], os.path.join(ROOT, "relay_server"),
          {"RELAY_PORT": PORT, "RELAY_DB_PATH": DB,
           "RELAY_LICENSE_ENABLED": "0"})   # 测试不走卡密授权（license_guard）
    time.sleep(4)

    if WITH_EXEC:
        print(">> 启动 dry_run 执行端…")
        spawn([PY, "main.py"], os.path.join(ROOT, "executor"),
              {"EXECUTOR_MODE": "dry_run", "EXECUTOR_POLL_INTERVAL": "2",
               "EXECUTOR_RELAY_URL": "http://127.0.0.1:%s" % PORT})
        time.sleep(6)

    try:
        r = subprocess.run([PY, os.path.join(ROOT, "tools", "test_console.py"),
                            "http://127.0.0.1:%s" % PORT], cwd=ROOT)
        code = r.returncode
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=5)
            except Exception:
                p.kill()
        print(">> 已关闭子进程")
    return code


if __name__ == "__main__":
    sys.exit(main())
