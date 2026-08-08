# Build context = repo root.  Render: Dockerfile path = mcp_server/Dockerfile
# NOTE: cadquery-ocp 7.8.x ships Linux wheels for x86_64 (amd64) only — no arm64.
# Render builds amd64 natively, so this works there as-is. On Apple Silicon you
# MUST build with:  docker build --platform linux/amd64 ...  (emulated via QEMU).
FROM python:3.13-slim

# Native runtime libs OCCT/cadquery needs (the slim image lacks them), plus git
# so pip can install the cq_gears/cq_warehouse plugins from their pinned commits.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglu1-mesa libxrender1 libxext6 libsm6 git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
# Install deps first (better layer caching), then the source.
COPY mcp_server/pyproject.toml mcp_server/pyproject.toml
COPY mcp_server/src mcp_server/src
RUN pip install --no-cache-dir ./mcp_server

# Single process: the MCP server serves /mcp + /viewer + /model + /version +
# /health + /export on one port (PORT, injected by the host). No Caddy, no
# separate viewer process — three-cad-viewer is fed by ocp_tessellate over HTTP.
ENV MCP_TRANSPORT=http
EXPOSE 8080
CMD ["python", "mcp_server/src/t2c_mcp.py"]
