# ប្រើប្រាស់ Base Image របស់ Python ជំនាន់ 3.10
FROM python:3.10-slim

# កំណត់ Working Directory នៅ​ក្នុង Container
WORKDIR /app

# តម្លើង System Dependencies ដែល​ចាំបាច់
# - ffmpeg: សម្រាប់​ដំណើរការ Audio/Video
# - tesseract-ocr: សម្រាប់​អាន​អក្សរ​ពី​រូបភាព (OCR)
# - tesseract-ocr-khm: តម្លើងភាសាខ្មែរសម្រាប់ Tesseract
# - poppler-utils: Library ដែល pdf2image ត្រូវការ​ដើម្បី​បំប្លែង PDF
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    tesseract-ocr \
    tesseract-ocr-khm \
    poppler-utils \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# ចម្លងឯកសារ requirements.txt ចូល​ទៅ​ក្នុង Container
COPY requirements.txt .

# តម្លើង Python libraries ដែល​បាន​កំណត់
RUN pip install --no-cache-dir -r requirements.txt

# ចម្លង​កូដ Bot របស់​អ្នក​ចូល​ទៅ​ក្នុង Container
COPY DocConvert.py .

# កំណត់ Command ដែល​ត្រូវ​រត់​នៅ​ពេល Container ចាប់ផ្តើម
CMD ["python", "DocConvert.py"]