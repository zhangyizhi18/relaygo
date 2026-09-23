# -*- coding: utf-8 -*-
"""离线回归总跑手（临时）。

逐个执行 tools/test_*.py；排除需要真机/联网/独占同花顺的：
  * test_ths_live.py       需要真机同花顺
  * test_ths_broker.py     真连同花顺做挂单演练（与运行中的执行端会互相抢 UI）
  * test_console.py        端到端控制台测试（需中转+执行端在线，且逐个 API 等待可达 90s）
  * test_signal.py         需运行中转(5010)发信号的端到端测试（离线跑会因连不上而失败）
每个子进程设 PYTHONIOENCODING=utf-8，输出汇总"文件 -> 通过/失败"。
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = os.path.join(ROOT, "venv", "Scripts", "python.exe")

SKIP = {"test_ths_live.py", "test_ths_broker.py", "test_console.py", "test_signal.py"}

files = sorted(f for f in os.listdir(HERE)
               if f.startswith("test_") and f.endswith(".py") and f not in SKIP)

env = dict(os.environ)
env["PYTHONIOENCODING"] = "utf-8"

results = []
for f in files:
    p = os.path.join(HERE, f)
    try:
        r = subprocess.run([PY, p], cwd=ROOT, env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=90)
        out = r.stdout.decode("utf-8", "replace")
        rc = r.returncode
    except subprocess.TimeoutExpired:
        out, rc = "", "TIMEOUT"
    # 抓最后几行里的"结果/通过/失败"
    tail = [l.strip() for l in out.strip().splitlines()[-6:] if l.strip()]
    summary = " | ".join(tail[-3:]) if tail else "(无输出)"
    results.append((f, rc, summary))
    print("[%s] %s" % ("OK  " if rc == 0 else "FAIL", f))
    print("       -> %s" % summary[:300])
    sys.stdout.flush()

bad = [f for f, rc, _ in results if rc != 0]
print("\n==== 共 %d 个文件，失败 %d 个 ====" % (len(results), len(bad)))
if bad:
    print("失败: " + ", ".join(bad))
sys.exit(1 if bad else 0)
