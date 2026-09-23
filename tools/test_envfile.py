# -*- coding: utf-8 -*-
"""
envfile.py 自测（不需要券商账号、不联网、不碰项目目录）。

覆盖：首次自动生成 / 编码判别（UTF-8 带 BOM、无 BOM、GBK）/ 两个文件分工与优先级 /
      系统环境变量优先 / 坏行不崩只告警 / 整数浮点列表容错 / 已存在文件只补不覆盖。

所有用例都写在临时目录里，不会在项目里留下 .env。

运行：venv\\Scripts\\python.exe tools\\test_envfile.py
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "executor"))

import envfile  # noqa: E402

PASS = []
FAIL = []
TMP = tempfile.mkdtemp(prefix="jq_envfile_")
MARKER = "TEST-MARKER-v1"
TEMPLATE = ("# __MARKER__\n"
            "# 这一段是说明文字\n"
            "JQ_TEST_A=1\n"
            "# 下面这项默认不生效\n"
            "#JQ_TEST_B=2\n").replace("__MARKER__", MARKER)


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  %s %s%s" % ("[OK ]" if cond else "[FAIL]", name,
                         ("  -> " + str(detail)) if (detail and not cond) else ""))


def reset_env():
    """清掉本测试用到的键，模拟「系统里从没设过环境变量」。"""
    for k in list(os.environ):
        if k.startswith(("EXECUTOR_", "JQ_TEST")):
            os.environ.pop(k, None)
    envfile.WARNINGS[:] = []
    os.environ.pop("JQ_ENVFILE_NOCREATE", None)


def setup_at(var, filename, template=None, marker=MARKER):
    """在临时目录里跑一次 setup()，返回 (info, 文件路径)。"""
    p = os.path.join(TMP, filename)
    os.environ[var] = p
    return envfile.setup(template or TEMPLATE, marker, var), p


print("\n[1] 首次运行自动生成")
reset_env()
info, path = setup_at("JQ_TEST_V1", "case1.env")
check("文件已生成", os.path.exists(path), path)
check("动作标记为 created", info["action"] == "created", info["action"])
check("带 UTF-8 BOM（记事本打开不乱码）", open(path, "rb").read().startswith(b"\xef\xbb\xbf"))
check("生效项已写入环境", os.environ.get("JQ_TEST_A") == "1", os.environ.get("JQ_TEST_A"))
check("注释掉的项不生效", os.environ.get("JQ_TEST_B") is None)
check("无告警", envfile.WARNINGS == [], envfile.WARNINGS)

print("\n[2] 系统环境变量优先：配置文件绝不覆盖")
reset_env()
os.environ["JQ_TEST_A"] = "来自系统环境变量"
setup_at("JQ_TEST_V2", "case2.env")
check("系统环境变量未被覆盖", os.environ["JQ_TEST_A"] == "来自系统环境变量",
      os.environ["JQ_TEST_A"])

print("\n[3] 编码判别")
reset_env()
p_utf = os.path.join(TMP, "utf8.env")
open(p_utf, "wb").write("\ufeff# 中文注释\nJQ_TEST_A=甲\n".encode("utf-8"))
p_gbk = os.path.join(TMP, "gbk.env")
open(p_gbk, "wb").write("# 中文注释\nJQ_TEST_A=C:\\同花顺软件\\xiadan.exe\n".encode("gbk"))
p_ascii = os.path.join(TMP, "ascii.env")
open(p_ascii, "w", encoding="ascii").write("JQ_TEST_A=plain\n")

check("UTF-8 带 BOM 判为 utf-8", envfile.decode(open(p_utf, "rb").read())[1] == "utf-8")
check("UTF-8 中文值读取正确", envfile.read(p_utf).get("JQ_TEST_A") == "甲",
      envfile.read(p_utf).get("JQ_TEST_A"))
check("GBK 判为 gbk（不被误判成 UTF-8）",
      envfile.decode(open(p_gbk, "rb").read())[1] == "gbk",
      envfile.decode(open(p_gbk, "rb").read())[1])
check("GBK 中文路径读取正确（关键：同花顺 三个字不能变乱码）",
      envfile.read(p_gbk).get("JQ_TEST_A") == "C:\\同花顺软件\\xiadan.exe",
      envfile.read(p_gbk).get("JQ_TEST_A"))
check("纯 ASCII 判为 utf-8", envfile.decode(open(p_ascii, "rb").read())[1] == "utf-8")

print("\n[4] 两个文件分工：.env + .env.local（后者优先）")
reset_env()
p_main = os.path.join(TMP, "main.env")
p_local = os.path.join(TMP, "main.env.local")
open(p_main, "w", encoding="utf-8-sig").write("JQ_TEST_A=主配置\nJQ_TEST_C=主配置独有\n")
open(p_local, "wb").write("JQ_TEST_A=向导填写\nJQ_TEST_D=向导独有\n".encode("gbk"))
merged, _ = envfile.load([p_main, p_local])
check(".env.local 覆盖同名项", merged.get("JQ_TEST_A") == "向导填写", merged.get("JQ_TEST_A"))
check(".env 独有项保留", merged.get("JQ_TEST_C") == "主配置独有", merged.get("JQ_TEST_C"))
check(".env.local 独有项生效", merged.get("JQ_TEST_D") == "向导独有", merged.get("JQ_TEST_D"))
reset_env()
envfile.load([p_main, p_local])
check("合并后生效值正确", os.environ.get("JQ_TEST_A") == "向导填写")

print("\n[5] 坏行不崩、只告警")
reset_env()
p_bad = os.path.join(TMP, "bad.env")
open(p_bad, "w", encoding="utf-8").write(
    "JQ_TEST_A=1\n这一行写错了没有等号\n= 缺少键名\nJQ_TEST_B=ok\n")
data = envfile.read(p_bad)
check("坏行被跳过、好行仍生效",
      data.get("JQ_TEST_A") == "1" and data.get("JQ_TEST_B") == "ok", data)
check("产生了告警且能打印", len(envfile.WARNINGS) >= 1 and "[!]" in envfile.warning_text(),
      envfile.WARNINGS)

print("\n[6] 类型转换容错")
reset_env()
os.environ["JQ_TEST_N"] = "abc"
os.environ["JQ_TEST_F"] = "三点五"
os.environ["JQ_TEST_L"] = "600519, 000001;300750"
check("整数写错回落默认值", envfile.get_int("JQ_TEST_N", 3) == 3)
check("浮点写错回落默认值", envfile.get_float("JQ_TEST_F", 0.03) == 0.03)
check("列表支持逗号与分号",
      envfile.get_list("JQ_TEST_L") == ["600519", "000001", "300750"],
      envfile.get_list("JQ_TEST_L"))
check("列表留空返回空列表", envfile.get_list("JQ_TEST_MISSING") == [])
check("写错的值有中文告警", any("不是整数" in w for w in envfile.WARNINGS), envfile.WARNINGS)
check("密钥脱敏只露尾号", "e-me" in envfile.mask("executor-key-2026-change-me"),
      envfile.mask("executor-key-2026-change-me"))
check("空密钥显示未设置", envfile.mask("") == "(未设置)")
check("短值不泄露内容", envfile.mask("abc") == "已设置", envfile.mask("abc"))

print("\n[7] 已存在的用户文件：只补不覆盖")
reset_env()
p_own = os.path.join(TMP, "own.env")
open(p_own, "w", encoding="utf-8").write("# 用户自己写的配置\nJQ_TEST_A=用户设置的值\n")
info, _ = setup_at("JQ_TEST_V7", "own.env")
check("动作标记为 appended", info["action"] == "appended", info["action"])
text = open(p_own, encoding="utf-8").read()
check("用户原有内容保留", "用户设置的值" in text)
check("重复项已被注释掉", "# JQ_TEST_A=1" in text,
      [l for l in text.splitlines() if "JQ_TEST_A" in l])
reset_env()
check("用户取值仍是最终生效值",
      envfile.read(p_own).get("JQ_TEST_A") == "用户设置的值",
      envfile.read(p_own).get("JQ_TEST_A"))
info2, _ = setup_at("JQ_TEST_V7", "own.env")
check("重复运行不会重复追加", info2["action"] == "exists", info2["action"])

print("\n[8] 补充段沿用文件原编码（不产生混合编码）")
reset_env()
p_gbk_own = os.path.join(TMP, "gbkown.env")
open(p_gbk_own, "wb").write("# 中文注释\nJQ_TEST_A=甲\n".encode("gbk"))
os.environ["JQ_TEST_V8"] = p_gbk_own
envfile.setup(TEMPLATE, MARKER, "JQ_TEST_V8")
check("GBK 文件补充后仍是 GBK",
      envfile.decode(open(p_gbk_own, "rb").read())[1] == "gbk",
      envfile.decode(open(p_gbk_own, "rb").read())[1])
check("补充后原值仍能正确读出",
      envfile.read(p_gbk_own).get("JQ_TEST_A") == "甲",
      envfile.read(p_gbk_own).get("JQ_TEST_A"))

print("\n[9] 禁止自动生成（Docker / 打包自检场景）")
reset_env()
os.environ["JQ_ENVFILE_NOCREATE"] = "1"
info, path9 = setup_at("JQ_TEST_V9", "case9.env")
check("未生成文件", not os.path.exists(path9), path9)
check("动作标记为 disabled", info["action"] == "disabled", info["action"])
check("仍可正常启动（取默认值）", info["applied"] == [], info["applied"])

print("\n" + "=" * 62)
print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：%s" % FAIL)
    sys.exit(1)
print("全部通过")
