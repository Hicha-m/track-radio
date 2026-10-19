FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app
WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
RUN groupadd --gid 10001 radio && useradd --uid 10001 --gid radio --no-create-home radio \
    && mkdir -p /app/data && chown radio:radio /app/data

FROM base AS test
COPY requirements-dev.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements-dev.lock
COPY app ./app
COPY alembic.ini pyproject.toml ./
COPY migrations ./migrations
COPY scripts ./scripts
COPY config ./config
COPY tests ./tests
CMD ["pytest", "--cov=app", "--cov-report=term-missing"]

FROM base AS runtime
ARG APP_REVISION=development
ENV APP_REVISION=$APP_REVISION
COPY app ./app
COPY alembic.ini pyproject.toml ./
COPY migrations ./migrations
COPY scripts ./scripts
COPY config ./config
USER radio
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && python scripts/seed.py && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 --limit-concurrency 24 --timeout-keep-alive 5 --no-access-log"]
