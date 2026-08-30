FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY budget-be/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY budget-be ./budget-be
COPY prompts ./prompts

EXPOSE 5004

CMD ["python", "budget-be/app.py"]
