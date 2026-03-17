# ── Stage 1: .NET SDK + Python base ──────────────────────────────────────────
# Use Microsoft's .NET 8 SDK image and add Python 3.12 on top.
# This avoids downloading .NET packages at runtime and keeps the layer cache clean.
FROM mcr.microsoft.com/dotnet/sdk:8.0 AS base

# Install Python 3.12 + pip
RUN apt-get update && apt-get install -y \
    python3.12 \
    python3.12-venv \
    python3-pip \
    curl \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Make python3.12 the default python
RUN update-alternatives --install /usr/bin/python python /usr/bin/python3.12 1 \
 && update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.12 1

# Install uv (fast Python package manager)
RUN curl -Lsf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

# ── Stage 2: Install Python dependencies ─────────────────────────────────────
WORKDIR /app

# Copy only dependency files first (better layer caching)
COPY pyproject.toml uv.lock ./

# Install all dependencies into the project venv
RUN uv sync --frozen --no-dev

# ── Stage 3: Copy application code ───────────────────────────────────────────
COPY . .

# ── Runtime config ────────────────────────────────────────────────────────────
# Port the web server listens on
EXPOSE 8000

# Health check — Railway/Render use this to know the container is ready
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
  CMD curl -f http://localhost:8000/health || exit 1

# Start the FastAPI server
# PORT is injected by Render/Railway at runtime; falls back to 8000 for local Docker
CMD ["sh", "-c", "uv run uvicorn api.index:app --host 0.0.0.0 --port ${PORT:-8000}"]
