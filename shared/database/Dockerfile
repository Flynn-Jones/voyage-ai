FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY database ./database
COPY expected_shared_data.json ./database/src/expected_shared_data.json

EXPOSE 6000

CMD ["sh", "-c", "python database/src/init_db.py && python database/src/app.py"]
