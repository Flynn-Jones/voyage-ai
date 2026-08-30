FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY init_db.py .
COPY db_routes.py .
COPY app.py .

EXPOSE 6003

CMD ["python", "app.py"]
