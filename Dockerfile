# ---------- 前端构建 ----------
FROM node:20-slim AS frontend
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---------- 后端运行 ----------
FROM python:3.11-slim AS backend
WORKDIR /app

# 系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ && rm -rf /var/lib/apt/lists/*

# Python 依赖
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 后端代码
COPY backend/ ./

# 前端构建产物
COPY --from=frontend /build/dist ./static/

# 数据持久化
RUN mkdir -p /app/data
VOLUME /app/data

ENV DB_PATH=/app/data/financecrew.db
ENV FRONTEND_DIST=/app/static
EXPOSE 8000

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
