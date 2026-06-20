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

ENV PORT=3040
EXPOSE ${PORT}

CMD uvicorn shuo.web:app --host 0.0.0.0 --port ${PORT}
