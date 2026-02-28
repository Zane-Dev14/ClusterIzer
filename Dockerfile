FROM python:3.11-slim AS base

LABEL maintainer="ClusterGPT" \
      description="Autonomous Kubernetes Auditor & Co-Pilot"

WORKDIR /opt/clustergpt

# Install OS deps (curl for healthchecks, kubectl for fallback)
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
    && curl -LO "https://dl.k8s.io/release/$(curl -Ls https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl" \
    && install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl \
    && rm kubectl \
    && apt-get purge -y curl && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ app/

# Non-root user for security
RUN useradd -r -s /bin/false clustergpt \
    && mkdir -p snapshots backups \
    && chown -R clustergpt:clustergpt /opt/clustergpt
USER clustergpt

ENTRYPOINT ["python", "-m", "app.main"]
CMD ["analyze", "--help"]
