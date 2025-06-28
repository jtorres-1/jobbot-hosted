FROM python:3.10-slim

# Install Chromium and required packages
RUN apt-get update && apt-get install -y \
    chromium \
    chromium-driver \
    libglib2.0-0 \
    libnss3 \
    libgconf-2-4 \
    libfontconfig1 \
    libxss1 \
    libappindicator1 \
    libindicator7 \
    libasound2 \
    libatk-bridge2.0-0 \
    libgtk-3-0 \
    fonts-liberation \
    xdg-utils \
    wget \
    unzip && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Set environment variable for Chromium
ENV CHROME_BIN=/usr/bin/chromium

# Copy files
COPY . /app
WORKDIR /app

# Install Python deps
RUN pip install --no-cache-dir -r requirements.txt

# Run the app
CMD ["python", "main.py"]
