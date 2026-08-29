FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY frontend-service/ ./frontend-service/

EXPOSE 3000

CMD ["python", "frontend-service/app.py"]
