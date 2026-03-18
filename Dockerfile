FROM python:3.12-slim

WORKDIR /app

# Ordner für den SQLite-Cache anlegen (damit dieser als Volume gemounted werden kann)
RUN mkdir -p /app/data
ENV DB_PATH=/app/data/.fakeguard_cache.db

# Dependencies installieren
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Anwendungscode kopieren
COPY . .

# Port des FastAPI-Backends freigeben
EXPOSE 8000

# Server starten
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
