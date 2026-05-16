FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

RUN pip install --no-cache-dir uv

# Resolve dependencies first so they cache independent of source changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY . .

EXPOSE 8501

# Streamlit Cloud / Railway / HF Spaces all set $PORT — honor it if present.
CMD ["sh", "-c", "uv run streamlit run app.py --server.address 0.0.0.0 --server.port ${PORT:-8501}"]
