FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY budget-be/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY budget-be ./budget-be
COPY prompts ./prompts

EXPOSE 5004

# gunicorn instead of Flask's dev server. Long timeout: a Multi-Agent run waits on several LLM calls.
CMD ["sh", "-c", "exec gunicorn --chdir budget-be --bind 0.0.0.0:${PORT:-5004} --workers 2 --threads 4 --timeout 300 --access-logfile - app:app"]
