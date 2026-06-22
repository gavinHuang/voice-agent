FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy only dependency definition first for layer caching
COPY pyproject.toml .

# Install dependencies (this layer is cached unless pyproject.toml changes)
# Create a minimal placeholder so pip install . works for deps only
RUN mkdir -p shuo && touch shuo/__init__.py && \
    mkdir -p monitor && touch monitor/__init__.py && \
    pip install --no-cache-dir . && \
    rm -rf shuo monitor

# Now copy actual source (changes here won't re-install deps)
COPY shuo/ ./shuo/
COPY monitor/ ./monitor/
COPY main.py .

# Re-install in-place (fast, deps already present)
RUN pip install --no-cache-dir --no-deps .

# Pre-download models so they're baked into the image.
# Without this, every new Cloud Run instance downloads them at startup,
# adding 8+ minutes before the service is ready to handle calls.
#
# 1. spacy en_core_web_sm: Kokoro TTS → misaki[en] → spacy needs this at runtime
RUN python -m spacy download en_core_web_sm
# 2. Kokoro TTS model from HuggingFace (hexgrad/Kokoro-82M, ~160 MB)
#    Download via huggingface_hub directly (no audio device needed).
RUN python -c "from huggingface_hub import snapshot_download; snapshot_download('hexgrad/Kokoro-82M'); print('Kokoro model cached successfully')"

ENV PORT=3040
EXPOSE ${PORT}

# Use main.py instead of bare uvicorn — it calls setup_logging() (required
# for application logs) and handles SIGTERM for graceful connection draining.
CMD ["python", "main.py"]
