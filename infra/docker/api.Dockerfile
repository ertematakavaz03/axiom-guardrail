FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY apps ./apps
COPY services ./services
COPY packages ./packages
COPY demos ./demos
COPY alembic.ini ./
COPY alembic ./alembic
RUN pip install --upgrade pip && pip install ".[langfuse]"
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn apps.api.app.main:app --host 0.0.0.0 --port 8000"]

