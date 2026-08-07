FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/models/huggingface

RUN sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g; s|security.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources /etc/apt/sources.list 2>/dev/null || true \
    && apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /service
ARG UV_DEFAULT_INDEX
RUN pip install --no-cache-dir --index-url https://pypi.tuna.tsinghua.edu.cn/simple uv==0.8.3
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev
# The lockfile pins CPU-only torch for Linux. GPU builds pass
# --build-arg TORCH_INDEX_URL=<cuda wheel index> (docker-compose.gpu.yml does
# this via the TORCH_INDEX_URL env override) to replace it with a CUDA wheel.
ARG TORCH_INDEX_URL=
# uv-created virtualenvs do not include pip, and the CPU torch pinned in the
# lockfile compares as newer than the equivalent +cu126 build, so --upgrade
# alone silently keeps the CPU wheel. --reinstall-package forces the CUDA
# swap for GPU builds (CPU builds skip this step entirely).
RUN if [ -n "${TORCH_INDEX_URL}" ]; then \
      uv pip install --python /service/.venv/bin/python --no-cache \
        --index-url "${TORCH_INDEX_URL}" \
        --reinstall-package torch \
        --upgrade "torch>=2.13.0,<2.14"; \
    fi
COPY app.py ./

EXPOSE 8100
CMD [".venv/bin/uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8100"]
