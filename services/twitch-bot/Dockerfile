FROM python:3.13-slim
WORKDIR /app

# Dependencies installieren
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Bot-Code kopieren
COPY services/twitch-bot/app/ ./bots/

# Start
CMD ["python", "bots/twitch_bot.py"]
