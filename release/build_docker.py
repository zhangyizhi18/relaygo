# -*- coding: utf-8 -*-
"""
中转服务 Docker 源码包打包脚本。

用法:
    python release/build_docker.py
产物:
    release/<版本号>-<时间戳>/jq-relay-docker-<版本号>-<时间戳>.zip

为什么是"源码包"而不是镜像 tar：
  本机不一定有 docker，且镜像分平台/架构。发出源码包，用户在自己的
  Linux 服务器上 `docker compose up -d --build` 现场构建，最通用。

包内结构（解压即可用）：
    jq-relay-docker-<版本>-<时间>/
      docker-compose.yml
      relay_server/           （含 Dockerfile 与全部源码）
      部署说明.txt
"""
import os
import shutil
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import release_common as rc  # noqa: E402

ROOT = rc.ROOT
TS = rc.release_ts()
OUT = rc.out_dir(TS)
ZIP_NAME = rc.package_name("relaygo-relay-docker", TS, ext="zip", platform=None)
ZIP_PATH = os.path.join(OUT, ZIP_NAME)
STAGE = os.path.join(rc.BUILD_DIR, "docker_stage_%s" % TS)   # 每次发版独立，免清理

# 只收 docker 运行必需的文件
# .py 全量收录（2026-09-12 起改为遍历：此前显式清单漏过 license_guard.py /
# security_code.py，导致 import 失败——新增模块不需要再记得改这里）；
# 非 .py 的固定文件单独列出。
RELAY_FIXED_FILES = ["Dockerfile", "requirements.docker.txt", ".dockerignore"]

def _relay_py_files():
    d = os.path.join(ROOT, "relay_server")
    return sorted(n for n in os.listdir(d) if n.endswith(".py"))

DOC = """========================================
RelayGo · 中转服务 Docker 版 {ver}
打包时间: {ts}
========================================

【适用】装了 docker + docker compose 的 Linux 服务器（推荐云服务器）。

【三步启动】
1. 解压本包，进入目录:
     unzip {zip}
     cd {folder}
2. 改密钥（docker-compose.yml 的 environment 段，务必改成自己的）:
     RELAY_SIGNAL_KEY   聚宽策略用的密钥
     RELAY_EXECUTOR_KEY 执行端用的密钥
     RELAY_WEB_SECRET   控制台登录会话密钥（随机字符串）
     RELAY_WEB_ADMIN_PASSWORD  控制台管理员密码
3. 构建并启动:
     docker compose up -d --build

【验证】
   curl http://127.0.0.1:5010/api/status        # 看到 "version":"{ver}" 即成功
   浏览器打开 http://<服务器IP>:5010/console/     # 控制台登录页
   （云服务器需在安全组放行 5010 端口）

【常用维护】
   docker compose logs -f          # 看日志
   docker compose restart          # 重启
   docker compose down             # 停止（数据在 named volume，不丢）
   改了源码后必须: docker compose up -d --build （只 up -d 不生效）

【数据不会丢】
   数据库落在 named volume jq-relay-data（容器内 /data/relay_data.db），
   容器重建/升级都不受影响。
"""


def main():
    folder = "%s-%s-%s" % ("jq-relay-docker", rc.VER, TS)
    pkg_root = os.path.join(STAGE, folder)

    # ---- 组织包内目录 ----
    os.makedirs(os.path.join(pkg_root, "relay_server"), exist_ok=True)
    shutil.copy2(os.path.join(ROOT, "docker-compose.yml"),
                 os.path.join(pkg_root, "docker-compose.yml"))
    for name in RELAY_FIXED_FILES + _relay_py_files():
        shutil.copy2(os.path.join(ROOT, "relay_server", name),
                     os.path.join(pkg_root, "relay_server", name))
    # 正式版锁：源码包同样锁定授权开关（config.py 检测本文件所在目录的 release.lock）
    with open(os.path.join(pkg_root, "relay_server", "release.lock"),
              "w", encoding="utf-8") as f:
        f.write("RelayGo release build lock. 授权开关已锁定，RELAY_LICENSE_ENABLED=0 无效。\n")
    with open(os.path.join(pkg_root, "部署说明.txt"), "w", encoding="utf-8") as f:
        f.write(DOC.format(ver=rc.VER, ts=TS, zip=ZIP_NAME, folder=folder))

    # ---- 打包 zip（顶层带版本目录，解压不会散落）----
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as z:
        for base, _dirs, files in os.walk(STAGE):
            for fn in files:
                full = os.path.join(base, fn)
                z.write(full, os.path.relpath(full, STAGE))

    size = os.path.getsize(ZIP_PATH)
    digest = rc.md5(ZIP_PATH)
    print("打包完成: %s (%d 字节)\nmd5: %s" % (ZIP_PATH, size, digest))
    _write_md5(OUT, ZIP_NAME, digest)


def _write_md5(out, filename, digest):
    with open(os.path.join(out, "md5.txt"), "a", encoding="utf-8") as f:
        f.write("%s  %s\n" % (digest, filename))


if __name__ == "__main__":
    main()
