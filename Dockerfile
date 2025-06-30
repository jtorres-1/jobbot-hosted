FROM python:3.10

# Set environment vars
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Install dependencies
RUN apt-get update && apt-get install -y \
    wget unzip curl gnupg2 -y \
    chromium chromium-driver

# Set display port
ENV DISPLAY=:99

# Set up app
WORKDIR /app
COPY . /app

RUN pip install --upgrade pip
RUN pip install -r requirements.txt

CMD ["python", "main.py"]
