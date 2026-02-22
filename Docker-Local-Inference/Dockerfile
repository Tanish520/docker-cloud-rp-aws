# Use an official Python runtime
FROM python:3.10-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    g++ \
    unixodbc-dev \
    curl \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

# Install Microsoft ODBC 18 drivers for Azure SQL (Modern Debian Method)
RUN curl https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft-archive-keyring.gpg \
    && echo "deb [arch=amd64,arm64,armhf signed-by=/usr/share/keyrings/microsoft-archive-keyring.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y msodbcsql18

# Copy requirements and install standard pipeline packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- INJECT CACHED MODEL ---
# The Azure Pipeline already downloaded and converted the model.

# Copy the actual code (translation.py, config.json, etc.)
COPY . .

# Run the script when the container starts
CMD ["python", "translation.py", "config.json"]