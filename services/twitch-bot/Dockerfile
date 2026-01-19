FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/twitch-bot/app ./app

RUN mkdir -p /app/logs

CMD ["python", "-m", "app.main"]
