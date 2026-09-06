FROM python:3.12-slim

# git für das Firmware-Repo, fonts-dejavu für den Splash-Schriftzug,
# der Rest sind Laufzeit-Abhängigkeiten der PlatformIO-Toolchains.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git ca-certificates curl fonts-dejavu-core \
        libgl1 libglib2.0-0 libusb-1.0-0 udev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

# API-Umgebung
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# PlatformIO in eigenem venv - es pinnt starlette<0.40 und kollidiert sonst
# mit FastAPI. Wird nur als Subprozess aufgerufen.
COPY requirements-pio.txt .
RUN python -m venv /opt/pio \
    && /opt/pio/bin/pip install --no-cache-dir -r requirements-pio.txt

COPY app ./app

# Bewusst NICHT auf den PATH: /opt/pio/bin/uvicorn wuerde sonst das uvicorn
# der API-Umgebung ueberschatten. builder.py ruft PlatformIO ueber PIO_BIN auf.
ENV PIO_BIN=/opt/pio/bin/platformio \
    DATA_DIR=/data \
    FIRMWARE_DIR=/firmware \
    LOGO_PATH=/assets/meshhessen_logo.png \
    PLATFORMIO_CORE_DIR=/pio \
    PYTHONUNBUFFERED=1

VOLUME ["/data", "/firmware", "/pio"]
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--timeout-keep-alive", "120"]
