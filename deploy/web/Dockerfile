# Runtime deps only: web/, runs/ and data/ are bind-mounted (see compose.yaml).
FROM python:3.12-slim
# triton JIT-compiles a small C launcher on the first generation
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /opt/web-requirements.txt
RUN pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cu130 -r /opt/web-requirements.txt
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HOME=/tmp HF_HOME=/hf
WORKDIR /app
CMD ["python", "-m", "uvicorn", "web.app:app", "--host", "0.0.0.0", "--port", "7862"]
