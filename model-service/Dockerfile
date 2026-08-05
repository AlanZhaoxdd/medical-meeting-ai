FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/models/huggingface

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /service
ARG UV_DEFAULT_INDEX
RUN pip install --no-cache-dir uv==0.8.3
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev
# The lockfile pins CPU-only torch for Linux. Pass
# --build-arg TORCH_INDEX_URL=https://download.pytorch.org/whl/cu126 to replace
# it with a CUDA wheel for GPU inference (docker-compose.gpu.yml does this).
ARG TORCH_INDEX_URL=
RUN if [ -n "${TORCH_INDEX_URL}" ]; then \
      .venv/bin/pip install --no-cache-dir --index-url "${TORCH_INDEX_URL}" \
        --upgrade "torch>=2.13.0,<2.14"; \
    fi
COPY app.py ./

EXPOSE 8100
CMD [".venv/bin/uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8100"]
