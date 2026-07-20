# Imagem do Jarvis com os modelos de voz já embutidos (pronta para a equipe).
FROM python:3.12-slim

# ffmpeg: garante a decodificação de áudio (webm/opus do navegador) pelo PyAV/Whisper.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Instala o pacote.
COPY pyproject.toml README.md ./
COPY jarvis ./jarvis
RUN pip install --no-cache-dir .

# Pré-baixa os modelos de voz para dentro da imagem (sem chave necessária).
ENV JARVIS_WHISPER_MODEL=small \
    JARVIS_PIPER_VOICE=pt_BR-faber-medium
RUN python -m jarvis.download

# Runtime: bind em 0.0.0.0 para ser alcançável fora do container.
ENV JARVIS_HOST=0.0.0.0 \
    JARVIS_PORT=8765
EXPOSE 8765

CMD ["jarvis"]
