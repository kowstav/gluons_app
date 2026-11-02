# API, MCP, webhooks, worker and one-shot jobs all run this image with different arguments.
# Build from the repository root: docker build -f apps/api/Dockerfile .
FROM python:3.13.3-slim@sha256:56a11364ffe0fee3bd60af6d6d5209eba8a99c2c16dc4c7c5861dc06261503cc AS build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.lock infra/docker/requirements-gcp.lock /app/locks/
COPY apps/api /app/apps/api
# Editable on purpose. config.py finds the repository from its own file path, and a copy in
# site-packages would resolve that to /usr/local, breaking `gluons migrate` and skill loading.
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -c /app/locks/requirements.lock -c /app/locks/requirements-gcp.lock \
       -e './apps/api[mcp,gcp,optimization,documents]'

FROM python:3.13.3-slim@sha256:56a11364ffe0fee3bd60af6d6d5209eba8a99c2c16dc4c7c5861dc06261503cc AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PATH=/opt/venv/bin:$PATH
WORKDIR /app
RUN useradd --create-home --uid 10001 gluons && mkdir /app/.gluons-data && chown gluons:gluons /app/.gluons-data
COPY --from=build /opt/venv /opt/venv
COPY --from=build /app/apps/api /app/apps/api
COPY packages /app/packages
COPY gluons_blueprint /app/gluons_blueprint
USER gluons
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"]
CMD ["gluons", "serve", "--host", "0.0.0.0", "--port", "8000"]
