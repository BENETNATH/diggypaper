FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# gosu : l'entrypoint démarre en root pour réparer les droits des volumes, puis passe en « app ».
RUN apt-get update && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin app

WORKDIR /app

# Dépendances + package (app/ requis pour le build du wheel)
COPY pyproject.toml constraints.txt ./
COPY app ./app
RUN pip install --upgrade pip && pip install -c constraints.txt '.[onnx]'

# Migrations + bootstrap
COPY alembic ./alembic
COPY alembic.ini ./
COPY entrypoint.sh ./
RUN sed -i "s/\r$//" entrypoint.sh && chmod +x entrypoint.sh \
    && mkdir -p /app/models /app/exports && chown -R app:app /app/models /app/exports

EXPOSE 8000
ENTRYPOINT ["./entrypoint.sh"]
