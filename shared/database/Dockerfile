FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY database ./database
COPY init_db.py .
COPY expected_shared_data.json .

EXPOSE 6000

CMD ["sh", "-c", "python init_db.py && python database/app.py"]
