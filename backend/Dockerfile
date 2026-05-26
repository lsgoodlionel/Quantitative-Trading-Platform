FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    POETRY_VERSION=1.8.4 \
    POETRY_HOME=/opt/poetry \
    POETRY_VENV_IN_PROJECT=1 \
    POETRY_NO_INTERACTION=1

RUN pip install --no-cache-dir poetry==${POETRY_VERSION}
ENV PATH="${POETRY_HOME}/bin:${PATH}"

WORKDIR /app
COPY pyproject.toml poetry.lock* ./

# --- 开发镜像 ---
FROM base AS development
RUN poetry install --no-root
COPY . .
RUN poetry install
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# --- 生产镜像 ---
FROM base AS production
RUN poetry install --no-root --only=main
COPY . .
RUN poetry install --only=main
# 非 root 用户运行
RUN useradd -m -r quantbot && chown -R quantbot /app
USER quantbot
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
