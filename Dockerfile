FROM python:3.12 AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libssl-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

RUN pip install uv

COPY pyproject.toml uv.lock ./
RUN uv pip install --system --no-cache .

COPY src/ src/

# ── runtime ──────────────────────────────────────────────────────────────────
FROM python:3.12 AS runtime

WORKDIR /app

# Install Node.js for MCP
RUN apt-get update && apt-get install -y curl && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs && \
    rm -rf /var/lib/apt/lists/*

# Lower OpenSSL security level to 1 so Python 3.12 can connect to
# MongoDB Atlas bare shard URLs (TLSV1_ALERT_INTERNAL_ERROR with SECLEVEL=2)
RUN sed -i 's/^\(MinProtocol\s*=\s*\).*/\1TLSv1.2/' /etc/ssl/openssl.cnf 2>/dev/null || true && \
    sed -i 's/^\(CipherString\s*=\s*\).*/\1DEFAULT@SECLEVEL=1/' /etc/ssl/openssl.cnf 2>/dev/null || true && \
    echo "" >> /etc/ssl/openssl.cnf && \
    echo "[system_default_sect]" >> /etc/ssl/openssl.cnf && \
    echo "CipherString = DEFAULT@SECLEVEL=1" >> /etc/ssl/openssl.cnf && \
    echo "MinProtocol = TLSv1.2" >> /etc/ssl/openssl.cnf

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder /app/src /app/src

ENV PYTHONPATH=/app/src
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

EXPOSE 8080

CMD ["uvicorn", "axiom.api.app:app", "--host", "0.0.0.0", "--port", "8080"]
