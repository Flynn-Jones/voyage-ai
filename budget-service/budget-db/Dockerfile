FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY init_db.py .

EXPOSE 6004

# Schema + seed (only when empty), then gunicorn. One worker: a single SQLite writer process.
CMD ["sh", "-c", "python init_db.py && exec gunicorn --bind 0.0.0.0:${PORT:-6004} --workers 1 --threads 4 --access-logfile - app:app"]