FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first so code edits do not bust the layer cache.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x /app/docker-entrypoint.sh

# Non-root. /app/var is the only path the app writes to, and it is where the volume
# gets mounted, so it must be owned by the runtime user.
RUN useradd --create-home --uid 10001 marketbot \
    && mkdir -p /app/var/charts \
    && chown -R marketbot:marketbot /app
USER marketbot

EXPOSE 8080

# Hits the app's own health endpoint rather than pretending to check something.
HEALTHCHECK --interval=60s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request,sys,os; \
url='http://127.0.0.1:'+os.environ.get('PORT','8080')+'/healthz'; \
sys.exit(0 if urllib.request.urlopen(url, timeout=5).status == 200 else 1)"

ENTRYPOINT ["/app/docker-entrypoint.sh"]
