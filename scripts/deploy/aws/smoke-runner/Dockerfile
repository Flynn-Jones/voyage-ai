FROM python:3.11-slim
# stdlib only; runs one smoke pass inside the VPC, then idles until scaled back to 0
RUN useradd --create-home smoke
USER smoke
WORKDIR /home/smoke
COPY smoke_runner.py .
CMD ["python", "-u", "smoke_runner.py"]
