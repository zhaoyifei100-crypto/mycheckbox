FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY sites.json .

# 仅放非敏感默认配置；Cookie 不进入镜像。
ENV PYTHONUNBUFFERED=1 \
    GCP_PROJECT_ID=project-048627af-e7d4-4972-9d3 \
    MYCHECKBOX_COOKIE_KEY_SECRET_ID=mycheckbox-cookie-key \
    PTSCHOOL_TIMEOUT_SECONDS=30

ENTRYPOINT ["python", "-m", "src.checkin"]
