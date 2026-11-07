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

# torch : la variante est un build-arg (TORCH_VARIANT=cpu|cuda, cf. docker-compose).
# cpu (défaut) : portable partout, ~200 Mo. cuda : serveurs GPU (NVIDIA driver
# compatible CUDA 12.8), l'embedding utilise le GPU automatiquement (~2,5 Go).
# Installé AVANT le package pour contrôler la wheel (l'index PyPI ne sert que
# la variante CUDA) ; les pins du reste viennent de constraints.txt.
ARG TORCH_VARIANT=cpu
RUN pip install --upgrade pip \
    && if [ "${TORCH_VARIANT}" = "cuda" ]; then \
         pip install --no-deps "torch==2.9.1+cu128" --index-url https://download.pytorch.org/whl/cu128; \
       else \
         pip install --no-deps "torch==2.9.1+cpu" --index-url https://download.pytorch.org/whl/cpu; \
       fi

# Dépendances + package (app/ requis pour le build du wheel)
COPY pyproject.toml constraints.txt ./
COPY app ./app
COPY VERSION ./
RUN pip install -c constraints.txt .

# Migrations + bootstrap
COPY alembic ./alembic
COPY alembic.ini ./
COPY entrypoint.sh ./
RUN sed -i "s/\r$//" entrypoint.sh && chmod +x entrypoint.sh \
    && mkdir -p /app/models /app/exports && chown -R app:app /app/models /app/exports

EXPOSE 8000
ENTRYPOINT ["./entrypoint.sh"]
