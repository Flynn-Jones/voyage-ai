# ==========================================
# Stage 1: Build & Dependency Wheel Builder
# ==========================================
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install build dependencies if needed
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install packages into a virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt


# ==========================================
# Stage 2: Minimal Runtime
# ==========================================
FROM python:3.11-slim AS runner

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Install curl for healthcheck calls
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy the pre-built virtual environment from builder stage
COPY --from=builder /opt/venv /opt/venv

# Copy the project metadata and source files needed for editable install
COPY app.py ./
COPY pyproject.toml ./
COPY src/ ./src/
COPY prompts/ ./prompts/

# Install the local package in editable mode within the copied venv
RUN pip install --no-cache-dir --no-deps -e .

EXPOSE 6002

CMD ["sh", "-c", "python -m app"]
