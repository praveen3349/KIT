FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY kit ./kit
COPY static ./static
RUN useradd -m app && mkdir -p /data && chown app /data
USER app
ENV DATA_DIR=/data
EXPOSE 8000
CMD ["sh", "-c", "uvicorn kit.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
