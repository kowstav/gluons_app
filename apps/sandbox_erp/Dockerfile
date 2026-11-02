# SIMULATOR, not ERPNext. Local development and tests only; never deployed to a hosted project.
# Build from the repository root: docker build -f apps/sandbox_erp/Dockerfile .
FROM python:3.13.3-slim@sha256:56a11364ffe0fee3bd60af6d6d5209eba8a99c2c16dc4c7c5861dc06261503cc AS build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1
COPY requirements.lock /tmp/requirements.lock
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -c /tmp/requirements.lock fastapi uvicorn

FROM python:3.13.3-slim@sha256:56a11364ffe0fee3bd60af6d6d5209eba8a99c2c16dc4c7c5861dc06261503cc AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PATH=/opt/venv/bin:$PATH SANDBOX_ERP_DB=/data/sandbox_erp.sqlite3
WORKDIR /app
RUN useradd --create-home --uid 10001 simulator && mkdir /data && chown simulator:simulator /data
COPY --from=build /opt/venv /opt/venv
COPY apps/sandbox_erp/sandbox_erp.py /app/sandbox_erp.py
USER simulator
EXPOSE 8100
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8100/health', timeout=4)"]
CMD ["uvicorn", "sandbox_erp:app", "--app-dir", "/app", "--host", "0.0.0.0", "--port", "8100"]
