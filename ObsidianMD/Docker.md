# Docker Deployment

> Zurück: [[README]] | Siehe auch: [[Konfiguration]], [[API]]

FakeNewsGuard ist als **Docker-Compose-Anwendung** mit 4 Services konzipiert.

---

## Services

```yaml
# docker-compose.yml

services:
  searxng:    # Selbst-gehostete Suchmaschine
  backend:    # FastAPI-Server
  frontend:   # Next.js-App
  telegram-bot:  # Telegram-Bot
```

---

## Service-Details

### searxng

```yaml
searxng:
  image: searxng/searxng
  ports:
    - "8080:8888"    # Extern 8080, intern 8888
  volumes:
    - ./searxng:/etc/searxng
```

Aggregiert Ergebnisse von Google, Bing, DuckDuckGo und weiteren Quellen. Kostenlos, keine API-Keys.

→ [[Websuche#SearXNG]]

---

### backend

```yaml
backend:
  build: .
  ports:
    - "8080:8000"
  env_file:
    - .env
  environment:
    - SEARXNG_URL=http://searxng:8888
  volumes:
    - fng_data:/app/data    # SQLite-Datenbanken persistent
  depends_on:
    - searxng
```

Das Backend schreibt alle SQLite-Datenbanken in `/app/data/` – das ist das persistente Volume.

---

### frontend

```yaml
frontend:
  build:
    context: ./frontend
    dockerfile: Dockerfile
  ports:
    - "3000:3000"
  environment:
    - NEXT_PUBLIC_API_URL=http://localhost:8080
  depends_on:
    - backend
```

---

### telegram-bot

```yaml
telegram-bot:
  build: .
  command: python telegram_bot.py
  env_file:
    - .env
  environment:
    - BACKEND_URL=http://backend:8000
  depends_on:
    - backend
```

→ [[Telegram-Bot]]

---

## Shared Volume

```yaml
volumes:
  fng_data:
    driver: local
```

Das `fng_data`-Volume wird von `backend` **und** `telegram-bot` gemountet. Beide können auf dieselben SQLite-Datenbanken zugreifen. Da SQLite im WAL-Modus läuft, ist gleichzeitiger Zugriff sicher.

---

## Dockerfile (Backend)

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Datenbankverzeichnis
RUN mkdir -p /app/data
VOLUME /app/data

EXPOSE 8000

CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

## Quick Start

```bash
# 1. Repo klonen
git clone https://github.com/…/FakeNewsGuard
cd FakeNewsGuard

# 2. Konfiguration
cp .env.example .env
# .env bearbeiten: API-Keys eintragen

# 3. Starten
docker compose up -d

# 4. Logs prüfen
docker compose logs -f backend

# 5. Öffnen
open http://localhost:3000
```

---

## Umgebungsvariablen für Docker

Alle Env-Vars werden aus `.env` geladen:

```bash
# .env
OPENROUTER_API_KEY=sk-or-...
SEARXNG_URL=http://searxng:8888   # Docker-interner Service-Name!
TELEGRAM_BOT_TOKEN=123:ABC...
JWT_SECRET=...
SECURE_COOKIES=false              # true wenn hinter HTTPS Reverse Proxy
```

**Wichtig:** Innerhalb von Docker muss `SEARXNG_URL=http://searxng:8888` (Service-Name), nicht `http://localhost:8888`.

---

## Produktions-Deployment

Für Produktion empfiehlt sich ein **Nginx-Reverse-Proxy**:

```nginx
server {
    listen 443 ssl;
    server_name fakeguard.example.com;

    location /api/ {
        proxy_pass http://backend:8000;
    }

    location / {
        proxy_pass http://frontend:3000;
    }
}
```

Dann:
- `SECURE_COOKIES=true` setzen
- `CORS_ORIGINS=https://fakeguard.example.com` setzen

---

## Verwandte Dokumente

- [[Konfiguration]] – Alle Umgebungsvariablen
- [[Websuche]] – SearXNG-Konfiguration
- [[Telegram-Bot]] – telegram-bot-Service
- [[API]] – Backend-Service Details
