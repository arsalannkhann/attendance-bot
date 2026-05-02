# Playwright base image ships Chromium + all required system libraries —
# avoids the brittle apt-get dance and wins ~90s of cold-start time.
FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Kolkata \
    PORT=10000

WORKDIR /app

# tzdata so Python's stdlib `zoneinfo` can resolve "Asia/Kolkata" from the
# filesystem; pip-level `tzdata` in requirements.txt is the in-process fallback.
# DEBIAN_FRONTEND=noninteractive + preseeding /etc/timezone *before* install
# so tzdata's postinst skips the interactive Area/City debconf prompt.
RUN echo "$TZ" > /etc/timezone \
 && DEBIAN_FRONTEND=noninteractive apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends tzdata \
 && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
 && dpkg-reconfigure -f noninteractive tzdata \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 10000

CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT}"]
