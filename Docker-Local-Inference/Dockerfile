# Use an official Python runtime
FROM python:3.10-slim

WORKDIR /app

# Install system dependencies and Microsoft ODBC 18 drivers for Azure SQL
RUN apt-get update && apt-get install -y g++ unixodbc-dev curl gnupg \
    && curl https://packages.microsoft.com/keys/microsoft.asc | apt-key add - \
    && curl https://packages.microsoft.com/config/debian/11/prod.list > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update && ACCEPT_EULA=Y apt-get install -y msodbcsql18

# Install Python packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- PRE-CACHE THE AI MODEL (Strict Latency Optimization) ---
RUN python -c "from transformers import MBartForConditionalGeneration, MBart50TokenizerFast; \
    MBart50TokenizerFast.from_pretrained('facebook/mbart-large-50-many-to-many-mmt'); \
    MBartForConditionalGeneration.from_pretrained('facebook/mbart-large-50-many-to-many-mmt')"

# Copy the actual code
COPY . .

# Run the script when the container starts
CMD ["python", "translation.py", "config.json"]