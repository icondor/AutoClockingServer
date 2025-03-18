# Use a Python base image with UTF-8 support and a compatible version for updated dependencies
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Set environment variables for locale and encoding
ENV LANG en_US.UTF-8
ENV LC_ALL en_US.UTF-8
ENV PYTHONUNBUFFERED 1

# Install system dependencies (locale, fonts, and font rendering libraries)
RUN apt-get update && apt-get install -y \
    locales \
    fonts-dejavu-core \
    fontconfig \
    libfreetype6 \
    && rm -rf /var/lib/apt/lists/* \
    && locale-gen en_US.UTF-8 \
    # Symlink DejaVuSans to match the macOS path expected by the Python code
    && mkdir -p /opt/homebrew/Cellar/font-dejavu/2.37/share/fonts/truetype \
    && ln -s /usr/share/fonts/truetype/dejavu/DejaVuSans.ttf /opt/homebrew/Cellar/font-dejavu/2.37/share/fonts/truetype/DejaVuSans.ttf \
    # Verify the font is available
    && if [ ! -f /usr/share/fonts/truetype/dejavu/DejaVuSans.ttf ]; then echo "DejaVuSans font not found!" && exit 1; fi

# Copy application files
COPY . /app

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Expose the port
EXPOSE 3001

# Command to run the application
CMD ["python", "TestServerApplication.py"]