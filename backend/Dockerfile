FROM python:3.12-slim

# Build-time commit SHA (passed from docker compose): lets the /ci-status endpoint
# report the CI state of the exact commit running.
ARG COMMIT_SHA=unknown
ENV COMMIT_SHA=$COMMIT_SHA

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# gosu : l'entrypoint démarre en root pour réparer les droits des volumes, puis passe en « app ».
# tesseract : OCR des PDF scannés (upload documents).
RUN apt-get update && apt-get install -y --no-install-recommends gosu \
    tesseract-ocr tesseract-ocr-fra \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin app

WORKDIR /app

# Dépendances + package (app/ requis pour le build du wheel)
COPY pyproject.toml constraints.txt ./
COPY app ./app
COPY VERSION ./
RUN pip install --upgrade pip && pip install -c constraints.txt .

# Migrations + bootstrap
COPY alembic ./alembic
COPY alembic.ini ./
COPY entrypoint.sh ./
RUN sed -i "s/\r$//" entrypoint.sh && chmod +x entrypoint.sh \
    && mkdir -p /app/models /app/exports && chown -R app:app /app/models /app/exports

EXPOSE 8000
ENTRYPOINT ["./entrypoint.sh"]
