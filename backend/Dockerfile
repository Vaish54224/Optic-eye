FROM python:3.12-slim

# Install system dependencies required for OpenCV
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run the backend service. Since Docker runs headless, pystray will fallback,
# and webcam mapping requires passing /dev/video0.
CMD ["python", "main.py"]
