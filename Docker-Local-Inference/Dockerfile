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

# Install Python packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- PRE-CACHE THE AI MODEL (Strict Latency Optimization) ---
# This ensures the 2.5GB model is inside the image and never downloaded at runtime
RUN python -c "from transformers import MBartForConditionalGeneration, MBart50TokenizerFast; \
    model_name = 'facebook/mbart-large-50-many-to-many-mmt'; \
    MBart50TokenizerFast.from_pretrained(model_name); \
    MBartForConditionalGeneration.from_pretrained(model_name)"

# Copy the actual code
COPY . .

# Run the script when the container starts
CMD ["python", "translation.py", "config.json"]