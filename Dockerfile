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
RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata \
 && rm -rf /var/lib/apt/lists/* \
 && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
 && echo $TZ > /etc/timezone

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 10000

CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT}"]
