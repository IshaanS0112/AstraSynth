FROM python:3.12-slim

# opencv-python-headless still links libGL-free, but numpy/opencv wheels need
# libgomp at runtime. Installing it explicitly keeps the image slim while
# avoiding the classic "libgomp.so.1: cannot open shared object file" crash.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

RUN useradd --create-home --uid 1000 astra \
    && mkdir -p /app/storage \
    && chown -R astra:astra /app
USER astra

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
