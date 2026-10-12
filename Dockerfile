FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_ENV=production \
    HOST=0.0.0.0 \
    PORT=8000 \
    WARD_DB_PATH=/data/ward_scheduler.db

WORKDIR /app
RUN addgroup --system app && adduser --system --ingroup app app \
    && mkdir -p /data && chown -R app:app /app /data

COPY --chown=app:app pyproject.toml ./
COPY --chown=app:app ward_api ./ward_api
COPY --chown=app:app scripts ./scripts

USER app
EXPOSE 8000
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=3)"

CMD ["python", "-m", "ward_api.server"]
