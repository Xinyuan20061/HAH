FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
# 云托管构建环境在国内，pypi.org 直连常超时（曾致 watchfiles 索引页 ReadTimeout 构建失败），
# 改用腾讯云 pip 镜像并提高 read 超时容错。
RUN pip install --no-cache-dir --timeout 120 -i https://mirrors.cloud.tencent.com/pypi/simple -r requirements.txt

COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./
COPY scripts ./scripts
COPY entrypoint.sh ./

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=15s CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.getenv('PORT','8000')+'/health/live',timeout=2)"

# WeChat Cloud Run injects PORT. Keep 8000 as local default.
CMD ["sh", "entrypoint.sh"]
