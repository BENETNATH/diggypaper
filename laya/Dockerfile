FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 HF_HOME=/hf

# PyTorch CPU (la roue par défaut embarque CUDA, ~2 Go de trop) puis le serveur Laya.
RUN pip install --upgrade pip \
    && pip install "torch==2.14.1" --index-url https://download.pytorch.org/whl/cpu \
    && pip install "laya[serve]==0.4.6"

# Les poids sont téléchargés au premier appel (volume /hf conservé entre redémarrages).
ENV LAYA_HOST=0.0.0.0 LAYA_PORT=8000 LAYA_PRELOAD=0 LAYA_THREADS=4
EXPOSE 8000
CMD ["laya-serve"]
