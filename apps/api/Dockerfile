# API, MCP, webhooks, worker and one-shot jobs all run this image with different arguments.
# Build from the repository root: docker build -f apps/api/Dockerfile .
FROM python:3.13.18-slim-trixie@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90 AS build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.lock infra/docker/requirements-gcp.lock /app/locks/
COPY apps/api /app/apps/api
# Editable on purpose. config.py finds the repository from its own file path, and a copy in
# site-packages would resolve that to /usr/local, breaking `gluons migrate` and skill loading.
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -c /app/locks/requirements.lock -c /app/locks/requirements-gcp.lock \
       -e './apps/api[mcp,gcp,optimization,documents]'

FROM python:3.13.18-slim-trixie@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90 AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PATH=/opt/venv/bin:$PATH
WORKDIR /app
RUN useradd --create-home --uid 10001 gluons && mkdir /app/.gluons-data && chown gluons:gluons /app/.gluons-data
COPY --from=build /opt/venv /opt/venv
# Keep build tooling out of the runtime and install the fixed Debian zlib package.
RUN apt-get update \
    && apt-get install -y --no-install-recommends 'zlib1g=1:1.3.dfsg+really1.3.1-1+deb13u1' \
    && rm -rf /var/lib/apt/lists/* \
    && /opt/venv/bin/python -m pip uninstall -y pip \
    && /usr/local/bin/python -m pip uninstall -y pip
COPY --from=build /app/apps/api /app/apps/api
COPY packages /app/packages
COPY gluons_blueprint /app/gluons_blueprint
USER gluons
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"]
CMD ["gluons", "serve", "--host", "0.0.0.0", "--port", "8000"]
