FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      build-essential \
      curl \
      fonts-noto-cjk \
      libgl1 \
      libglib2.0-0 \
      libgomp1 \
      poppler-utils \
      tesseract-ocr \
      tesseract-ocr-chi-sim \
    && rm -rf /var/lib/apt/lists/*

ARG UV_DEFAULT_INDEX
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./

EXPOSE 8000
CMD ["sh", "-c", ".venv/bin/alembic upgrade head && exec .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000"]
