# Gebruik een lichte, officiële Python image (werkt ook perfect op ARM/Raspberry Pi)
FROM python:3.11-slim

# Stel de werkmap in binnen de container
WORKDIR /app

# Kopieer de requirements en installeer ze
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Kopieer jouw bot-script naar de container
COPY vinted_bot.py .

# Zorg ervoor dat Python output direct naar de console stuurt (handig voor docker logs)
ENV PYTHONUNBUFFERED=1

# Start de bot
CMD ["python", "vinted_bot.py"]