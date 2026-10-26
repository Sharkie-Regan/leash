FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY leash/ leash/
COPY web/ web/
ENV LEASH_DATA_DIR=/data
VOLUME /data
EXPOSE 8000
CMD ["sh", "-c", "uvicorn leash.api.app:create_app --factory --host 0.0.0.0 --port ${PORT:-8000}"]
