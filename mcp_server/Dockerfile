# Build context = repo root.  Render: Dockerfile path = mcp_server/Dockerfile
# NOTE: cadquery-ocp 7.8.x ships Linux wheels for x86_64 (amd64) only — no arm64.
# Render builds amd64 natively, so this works there as-is. On Apple Silicon you
# MUST build with:  docker build --platform linux/amd64 ...  (emulated via QEMU).
FROM python:3.13-slim

# Native runtime libs OCCT/cadquery needs (the slim image lacks them).
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglu1-mesa libxrender1 libxext6 libsm6 \
    && rm -rf /var/lib/apt/lists/*

# Caddy binary straight from the official image (avoids apt-repo setup).
COPY --from=caddy:2 /usr/bin/caddy /usr/bin/caddy

WORKDIR /app
# Install deps first (better layer caching), then the source.
COPY mcp_server/pyproject.toml mcp_server/pyproject.toml
COPY mcp_server/src mcp_server/src
RUN pip install --no-cache-dir ./mcp_server

COPY mcp_server/Caddyfile /etc/caddy/Caddyfile

ENV MCP_TRANSPORT=http \
    MCP_HOST=127.0.0.1 \
    MCP_PORT=9000

# Three processes: viewer (3939) + MCP server (9000) + Caddy (public $PORT).
# Caddy runs in the foreground (exec) so it is the container's main process.
CMD ["bash", "-c", "python -m ocp_vscode --host 127.0.0.1 --port 3939 & python mcp_server/src/t2c_mcp.py & exec caddy run --config /etc/caddy/Caddyfile"]
