FROM node:20-alpine AS builder

WORKDIR /app

# Abhängigkeiten installieren
COPY package*.json ./
RUN npm ci

# Quellcode kopieren und bauen
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

# Runner-Image (kleinerer Footprint)
FROM node:20-alpine AS runner

WORKDIR /app
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

# Gebaute Artefakte vom Builder-Image übernehmen
COPY --from=builder /app/public ./public
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static

# Host auf 0.0.0.0 setzen, damit Next.js von außen (Host) erreichbar ist
ENV HOSTNAME="0.0.0.0"
ENV PORT=3000

# Next.js Port freigeben
EXPOSE 3000

# Next.js Server starten (Standalone-Modus ist deutlich schneller und schlanker)
CMD ["node", "server.js"]
