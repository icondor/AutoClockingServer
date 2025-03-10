FROM python:3.9-slim

# Install system dependencies for packages like pandas and openpyxl
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    libatlas-base-dev \
    gcc \
    libc6 \
    libxml2 \
    libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root user for security
RUN useradd -m appuser

WORKDIR /app

# Copy requirements first to leverage Docker cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY TestServerApplication.py .

# Set environment variable to avoid Python buffering logs
ENV PYTHONUNBUFFERED=1

# Set ownership to non-root user
RUN chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# Expose port
EXPOSE 3000

# Install Gunicorn for production-ready Flask server
RUN pip install gunicorn

# Start the Flask application with Gunicorn
CMD ["gunicorn", "--bind", "0.0.0.0:3000", "TestServerApplication:app"]

