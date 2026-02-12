FROM python:3.10-slim

WORKDIR /app

# តម្លើង System Dependencies និងកំណត់ PATH
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    tesseract-ocr \
    tesseract-ocr-khm \
    poppler-utils \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# កំណត់ Port សម្រាប់ Render (ទោះជា Bot មិនប្រើ Port ក៏ដោយ ក៏ត្រូវការសម្រាប់ Health Check)
EXPOSE 8080

CMD ["python", "DocConvert.py"]
