# Use a Python base image with UTF-8 support
FROM python:3.9-slim

# Set working directory
WORKDIR /app

# Set environment variables for locale and encoding
ENV LANG C.UTF-8
ENV LC_ALL C.UTF-8
ENV PYTHONUNBUFFERED 1

# Install system dependencies (for locale and optional fonts)
RUN apt-get update && apt-get install -y \
    locales \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/* \
    && locale-gen en_US.UTF-8

# Copy application files
COPY . /app

# Copy the font file into the container
RUN mkdir -p /app/fonts
COPY fonts/ArialUnicode.ttf /app/fonts/ArialUnicode.ttf

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Expose the port
EXPOSE 3001

# Command to run the application
CMD ["python", "app.py"]