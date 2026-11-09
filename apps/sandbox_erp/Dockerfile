# SIMULATOR, not ERPNext. Local development and tests only; never deployed to a hosted project.
# Build from the repository root: docker build -f apps/sandbox_erp/Dockerfile .
FROM python:3.13.18-slim-trixie@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90 AS build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1
COPY requirements.lock /tmp/requirements.lock
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -c /tmp/requirements.lock fastapi uvicorn

FROM python:3.13.18-slim-trixie@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90 AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PATH=/opt/venv/bin:$PATH SANDBOX_ERP_DB=/data/sandbox_erp.sqlite3
WORKDIR /app
RUN useradd --create-home --uid 10001 simulator && mkdir /data && chown simulator:simulator /data
COPY --from=build /opt/venv /opt/venv
# Runtime does not need pip; retain only the fixed Debian zlib package.
RUN apt-get update \
    && apt-get install -y --no-install-recommends 'zlib1g=1:1.3.dfsg+really1.3.1-1+deb13u1' \
    && rm -rf /var/lib/apt/lists/* \
    && /opt/venv/bin/python -m pip uninstall -y pip \
    && /usr/local/bin/python -m pip uninstall -y pip
COPY apps/sandbox_erp/sandbox_erp.py /app/sandbox_erp.py
USER simulator
EXPOSE 8100
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8100/health', timeout=4)"]
CMD ["uvicorn", "sandbox_erp:app", "--app-dir", "/app", "--host", "0.0.0.0", "--port", "8100"]
