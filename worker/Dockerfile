FROM python:3.11-slim

WORKDIR /app

# Install system dependencies including tmux (for agent_farm mode)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    tmux \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for layer caching
COPY worker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy worker code
COPY worker/ .

# Copy agent_farm module for multi-agent orchestration
COPY agent_farm/ /app/agent_farm/

CMD ["python", "main.py"]
