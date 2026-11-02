FROM python:3.13.3-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY apps/api /app/apps/api
COPY apps/sandbox_erp /app/apps/sandbox_erp
COPY packages /app/packages
COPY gluons_blueprint /app/gluons_blueprint
COPY requirements.lock /app/requirements.lock
RUN pip install --no-cache-dir -c requirements.lock './apps/api[mcp,gcp,optimization,documents]' \
    && useradd --create-home --uid 10001 gluons \
    && mkdir /app/.gluons-data && chown gluons:gluons /app/.gluons-data
USER gluons
EXPOSE 8000
CMD ["gluons", "serve", "--host", "0.0.0.0", "--port", "8000"]
