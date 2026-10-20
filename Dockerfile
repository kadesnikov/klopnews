FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-inter fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml ./
COPY klopnews ./klopnews
COPY config ./config
RUN pip install --no-cache-dir .
ENV DATA_DIR=/data SOURCES_FILE=/app/config/sources.yaml PYTHONUNBUFFERED=1
CMD ["uvicorn", "klopnews.web:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
