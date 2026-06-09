FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy package definition first for layer caching
COPY pyproject.toml .

# Copy source packages
COPY shuo/ ./shuo/
COPY monitor/ ./monitor/
COPY simulator/ ./simulator/
COPY main.py .

# Install the package and dependencies
RUN pip install --no-cache-dir -e .

EXPOSE 3040

CMD ["uvicorn", "shuo.web:app", "--host", "0.0.0.0", "--port", "3040"]
