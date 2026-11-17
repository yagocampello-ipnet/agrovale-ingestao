FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY script-ingestao.py .

CMD ["python", "script-ingestao.py"]