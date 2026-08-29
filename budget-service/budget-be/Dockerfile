FROM python:3.11-slim

WORKDIR /app

COPY budget-be/requirements.txt ./requirements.txt

RUN pip install --no-cache-dir -r requirements.txt

COPY budget-be ./budget-be
COPY prompts/service ./prompts/service
COPY prompts/lab4 ./prompts/lab4

EXPOSE 5004

CMD ["python", "budget-be/app.py"]