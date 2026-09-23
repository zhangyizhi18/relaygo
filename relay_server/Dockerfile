# 聚宽中转下单 · 中转服务 Docker 镜像
# 构建:  docker build -t jq-relay ./relay_server
# 运行:  docker run -d --name jq-relay -p 5010:5010 \
#          -v jq-relay-data:/data \
#          -e RELAY_SIGNAL_KEY=改成你的聚宽密钥 \
#          -e RELAY_EXECUTOR_KEY=改成你的执行端密钥 \
#          -e RELAY_WEB_SECRET=改成一段随机字符串 \
#          -e RELAY_WEB_ADMIN_PASSWORD=改成你的管理员密码 \
#          jq-relay
# Web 控制台: 浏览器打开 http://<服务器IP>:5010/console/
FROM python:3.12-slim

WORKDIR /app

# 时区：容器默认 UTC，导致信号时间戳比北京时间慢 8 小时（2026-09-12 修复）
RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*
ENV TZ=Asia/Shanghai

COPY requirements.docker.txt ./
RUN pip install --no-cache-dir -r requirements.docker.txt gunicorn

# 业务代码：全量收录本目录 *.py（web 控制台/任务通道/授权守卫/对账等都在这里，
# 显式逐个 COPY 曾漏过新增模块导致构建失败，2026-09-12 起改为通配）
COPY *.py ./

# 数据库落在挂载卷里，容器重建数据不丢
# JQ_ENVFILE_NOCREATE=1：容器里不自动生成 .env（配置请用环境变量或 compose 的 env_file 传）
ENV RELAY_DB_PATH=/data/relay_data.db \
    RELAY_HOST=0.0.0.0 \
    RELAY_PORT=5010 \
    JQ_ENVFILE_NOCREATE=1
VOLUME ["/data"]

EXPOSE 5010

# gunicorn: gthread 模式（--threads）——Web 控制台的查询/下单接口会阻塞等待执行端回报，
# 多线程保证这类等待不会拖死整个 worker；timeout 给足，避免长查询被误杀。
# --keep-alive 10：服务端保持 keep-alive 连接的等待时间（默认仅 2s），
# 让浏览器/控制台这类短连接客户端复用连接更顺；执行端已显式 Connection: close 不依赖它。
CMD ["gunicorn", "--workers", "2", "--threads", "8", "--timeout", "120", \
     "--keep-alive", "10", "--bind", "0.0.0.0:5010", "app:app"]
