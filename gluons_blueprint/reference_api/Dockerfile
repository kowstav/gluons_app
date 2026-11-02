FROM python:3.12-slim
WORKDIR /app
COPY reference_api/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY reference_api ./reference_api
COPY skills/catalog.json ./skills/catalog.json
ENV PORT=8080
EXPOSE 8080
CMD ["sh", "-c", "uvicorn reference_api.app:app --host 0.0.0.0 --port ${PORT}"]
