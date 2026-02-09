FROM python:3.11-slim

# Install Chrome dependencies
RUN apt-get update && apt-get install -y \
    wget \
    gnupg2 \
    unzip \
    curl \
    fonts-liberation \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libatspi2.0-0 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libgbm1 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxkbcommon0 \
    libxrandr2 \
    xdg-utils \
    --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Install Chrome
RUN wget -q https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
    && dpkg -i google-chrome-stable_current_amd64.deb || apt-get -fy install \
    && rm google-chrome-stable_current_amd64.deb

# Install ChromeDriver that matches Chrome version
RUN CHROME_VERSION=$(google-chrome --version | grep -oP '\d+\.\d+\.\d+') \
    && DRIVER_URL="https://storage.googleapis.com/chrome-for-testing-public/${CHROME_VERSION}.0/linux64/chromedriver-linux64.zip" \
    && echo "Downloading ChromeDriver from: $DRIVER_URL" \
    && wget -q "$DRIVER_URL" -O /tmp/chromedriver.zip || true \
    && if [ -f /tmp/chromedriver.zip ]; then \
         unzip /tmp/chromedriver.zip -d /tmp/ \
         && mv /tmp/chromedriver-linux64/chromedriver /usr/local/bin/ \
         && chmod +x /usr/local/bin/chromedriver \
         && rm -rf /tmp/chromedriver*; \
       else \
         echo "Falling back to chromedriver via pip"; \
       fi

# Set up app
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install chromedriver-autoinstaller as fallback
RUN pip install chromedriver-autoinstaller

COPY . .

ENV PORT=5000
ENV PYTHONUNBUFFERED=1

EXPOSE $PORT

CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:5000", "--timeout", "120", "--workers", "2"]
