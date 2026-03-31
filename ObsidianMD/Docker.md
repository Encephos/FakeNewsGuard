# Docker Deployment

> Zurück: [[README]] | Siehe auch: [[Konfiguration]], [[API]]

FakeNewsGuard ist als **Docker-Compose-Anwendung** mit 7 Services konzipiert (+ 1 einmaliger Migration-Job als optionales Profile).

---

## Services

```yaml
# docker-compose.yml

services:
  # ── Infrastruktur ─────────────────────────────────────────────────
  postgres:       # PostgreSQL 16 – primäre Datenbank (Produktions-Backend)
  valkey:         # Valkey (Redis-kompatibel) – Cache-Backend
  tor:            # Tor-Proxy für IP-Rotation beim Scraping
  searxng:        # Selbst-gehostete Suchmaschine
  # ── Anwendung ─────────────────────────────────────────────────────
  backend:        # FastAPI-Server
  frontend:       # Next.js-App
  telegram-bot:   # Telegram-Bot
  # ── Migration (Profile: migration) ───────────────────────────────
  migrate:        # Einmaliger SQLite → PostgreSQL Migration-Job
```

---

## Service-Details

### postgres

```yaml
postgres:
  image: postgres:16-alpine
  environment:
    POSTGRES_DB: fakeguard
    POSTGRES_USER: fakeguard
    POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-changeme}
  volumes:
    - pg_data:/var/lib/postgresql/data
```

Produktions-Datenbank. Wird via `DB_BACKEND=postgres` aktiviert. Im Dev-Modus (SQLite) optional.

---

### valkey

```yaml
valkey:
  image: valkey/valkey:8-alpine
  volumes:
    - valkey_data:/data
```

Redis-kompatibler Cache. Wird via `CACHE_BACKEND=valkey` aktiviert. Im Dev-Modus wird SQLite-Cache genutzt.

---

### tor

```yaml
tor:
  image: dperson/torproxy:latest
  environment:
    - TORRC=MaxCircuitDirtiness 60    # Neue Circuit alle 60s → IP-Rotation
```

Anonymisierungs-Proxy für Scraping-Anfragen. Verhindert IP-Blocking durch Zielseiten.

---

### searxng

```yaml
searxng:
  image: searxng/searxng:latest
  ports:
    - "8888:8080"    # Extern 8888, intern 8080
  volumes:
    - ./searxng:/etc/searxng
  depends_on:
    - valkey
```

Aggregiert Ergebnisse von Google, Bing, DuckDuckGo und weiteren Quellen. Kostenlos, keine API-Keys. Nutzt Valkey als Session-Cache.

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
    - SEARXNG_URL=http://searxng:8080
    - VALKEY_URL=redis://valkey:6379/0
    - POSTGRES_HOST=postgres
  volumes:
    - fng_data:/app/data    # Fallback-Volume für SQLite-Dev-Mode
  depends_on:
    postgres:
      condition: service_healthy
    searxng:
      condition: service_started
```

Das Backend nutzt PostgreSQL + Valkey in Produktion. Im Dev-Modus (`DB_BACKEND=sqlite`) werden alle Datenbanken in `/app/data/` geschrieben.

---

### frontend

```yaml
frontend:
  build:
    context: ./frontend
    dockerfile: Dockerfile
  ports:
    - "3000:3000"
  env_file:
    - .env
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
    - POSTGRES_HOST=postgres
    - VALKEY_URL=redis://valkey:6379/0
  volumes:
    - fng_data:/app/data
  depends_on:
    postgres:
      condition: service_healthy
    backend:
      condition: service_started
```

→ [[Telegram-Bot]]

---

### migrate (Profile: migration)

Einmaliger Job für SQLite → PostgreSQL Datenmigration:

```bash
docker compose run --rm migrate
```

Liest alte SQLite-Dateien aus `fng_data`-Volume und schreibt sie nach PostgreSQL.

---

## Volumes

```yaml
volumes:
  fng_data:     # SQLite-Dateien (Dev/Fallback) + shared zwischen backend & telegram-bot
  valkey_data:  # Valkey-Persistenz
  pg_data:      # PostgreSQL-Datenbankdateien
```

Das `fng_data`-Volume wird von `backend`, `telegram-bot` und `migrate` gemountet. Im Produktionsmodus (PostgreSQL + Valkey) dient es nur als Fallback.

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
SEARXNG_URL=http://searxng:8080   # Docker-interner Service-Name + interner Port 8080!
VALKEY_URL=redis://valkey:6379/0
POSTGRES_HOST=postgres
POSTGRES_PASSWORD=changeme
TELEGRAM_BOT_TOKEN=123:ABC...
JWT_SECRET=...
SECURE_COOKIES=false              # true wenn hinter HTTPS Reverse Proxy
# DB_BACKEND=postgres             # aktiviert PostgreSQL statt SQLite
# CACHE_BACKEND=valkey            # aktiviert Valkey statt SQLite-Cache
```

**Wichtig:** Innerhalb von Docker müssen Service-Namen statt `localhost` verwendet werden. SearXNG ist intern auf Port `8080` (nicht `8888` – das ist der externe Port).

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
