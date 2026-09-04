# Pinned to Debian 12 (bookworm): Playwright 1.49's `install --with-deps` package
# list predates Debian 13 (trixie) and fails there on renamed font packages.
FROM python:3.13-slim-bookworm

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Green Apple sits behind Cloudflare, so the scraper drives a headless Chromium.
# Install the browser and its system libraries into the image.
RUN playwright install --with-deps chromium

COPY . .

CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
