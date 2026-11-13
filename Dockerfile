FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY bot ./bot
COPY migrations ./migrations
COPY alembic.ini .
# Сначала обновляем структуру базы, затем запускаем бота.
# exec: бот становится главным процессом контейнера и корректно завершается по «Остановить бота».
CMD ["sh", "-c", "alembic upgrade head && exec python -m bot"]
