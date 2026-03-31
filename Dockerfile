FROM python:3.12-slim

WORKDIR /app

# Ordner für den SQLite-Cache anlegen (damit dieser als Volume gemounted werden kann)
RUN mkdir -p /app/data
ENV DB_PATH=/app/data/.fakeguard_cache.db
ENV ARCHIVE_DB_PATH=/app/data/.fakeguard_archive.db
ENV USERS_DB_PATH=/app/data/.fakeguard_users.db

# System-Dependencies fuer Media Ingestion (ffmpeg fuer yt-dlp Audio-Extraktion)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Python-Dependencies installieren
COPY requirements.txt requirements-media.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-media.txt

# Anwendungscode kopieren
COPY . .

# Port des FastAPI-Backends freigeben
EXPOSE 8000

# Server starten
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
