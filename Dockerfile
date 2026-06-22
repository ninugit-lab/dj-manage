# ── Stage 1: Tailwind CSS build ──────────────────────────
FROM node:20-slim AS css
WORKDIR /build
COPY app/theme/static_src/package*.json ./app/theme/static_src/
RUN cd app/theme/static_src && npm install
COPY app ./app
RUN cd app/theme/static_src && npx tailwindcss \
    -i ./src/styles.css -o ../static/css/dist/styles.css --minify

# ── Stage 2: Python runtime ──────────────────────────────
FROM python:3.12-slim
WORKDIR /app

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
 && rm -rf /var/lib/apt/lists/*

# Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project
COPY app .

# Copy built Tailwind CSS from the css stage (no Node in runtime)
COPY --from=css /build/app/theme/static/css/dist/styles.css ./theme/static/css/dist/styles.css

# Make entrypoint executable
RUN chmod +x /app/entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]
