FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY init_db.py .

EXPOSE 6004

CMD ["sh", "-c", "python init_db.py && python app.py"]