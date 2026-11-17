FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core fonts-montserrat ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

ENV DATA_DIR=/data \
    PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python", "-m", "liveclips.main"]
