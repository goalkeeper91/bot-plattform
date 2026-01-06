# Base Image
FROM python:3.13-slim

# Arbeitsverzeichnis
WORKDIR /app

# Dependencies kopieren
COPY requirements.txt .

# Installieren
RUN pip install --no-cache-dir -r requirements.txt

# App Code kopieren
COPY services/discord-bot/app/ ./bots/

# Start Command
CMD ["python", "bots/discord_bot.py"]
