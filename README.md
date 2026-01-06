# Bot Platform

**Discord & Twitch Bots** – Python-basiert, Docker-ready, mit gemeinsamer Event-Struktur.

---

## Projektübersicht

Dieses Repository enthält:

- `services/discord-bot` – Discord Bot
- `services/twitch-bot` – Twitch Bot
- `shared/` – Gemeinsamer Code (Event-Modelle, Config, Redis-Helfer)
- `docker/` – Docker Compose Setup & Umgebungsvariablen
- `scripts/` – Hilfsskripte (z.B. DB Migrationen)

---

## Voraussetzungen

- Python 3.11+
- Docker & Docker Compose
- Git
- (Optional) PyCharm oder andere IDE

---

## Setup lokal

1. Repo klonen:

```bash
git clone https://github.com/deinname/bot-platform.git
cd bot-platform
