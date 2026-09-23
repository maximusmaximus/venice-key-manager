# Multi-stage production container for Venice Key Manager
FROM python:3.12-slim AS builder

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends gcc libffi-dev && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

FROM python:3.12-slim AS runner

WORKDIR /app
COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH
ENV PYTHONUNBUFFERED=1

COPY venice_key_manager/ venice_key_manager/
COPY pyproject.toml .
COPY README.md .
COPY LICENSE .

RUN pip install --no-cache-dir -e .

EXPOSE 8660 8661

VOLUME ["/app/data"]

ENTRYPOINT ["venice-key-manager"]
CMD ["web", "--host", "0.0.0.0", "--port", "8660"]
