FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY access-service/ ./access-service/

EXPOSE 5000

CMD ["python", "access-service/app.py"]
