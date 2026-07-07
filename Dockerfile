# =============================================================================
# Dockerfile - Builds the PHP Symfony application container
# =============================================================================
#
# This is a simple development Dockerfile that:
#   1. Starts from the official PHP 8.3 CLI image
#   2. Installs system dependencies (unzip for Composer, zip extension for packages)
#   3. Copies Composer (PHP package manager) from the official Composer image
#   4. Copies the Symfony application code
#   5. Runs Composer to install PHP dependencies
#   6. Starts PHP's built-in web server on port 8080
#
# NOTE: This uses PHP's built-in web server, which is fine for development
# but NOT suitable for production. For production, use Nginx + PHP-FPM or
# a similar setup.
#
# BUILD CONTEXT:
#   The build context is the ambient-scribe/ directory (set in docker-compose.yml).
# =============================================================================

# Use PHP 8.3 CLI as the base image (no Apache/Nginx - we use the built-in server)
FROM php:8.3-cli

# Install system packages needed by PHP extensions and Composer
RUN apt-get update && apt-get install -y \
    unzip \
    libzip-dev \
    curl \
    && docker-php-ext-install zip \
    && rm -rf /var/lib/apt/lists/*

# Copy Composer binary from the official Composer Docker image
# This avoids having to install Composer manually
COPY --from=composer:latest /usr/bin/composer /usr/bin/composer

WORKDIR /app

# Copy the entire Symfony application into the container
COPY . /app/

# Install PHP dependencies (--no-dev skips test/dev packages, --optimize-autoloader for speed)
# strands-php-client resolves from the tagged Composer constraint in composer.lock.
RUN composer install --no-dev --optimize-autoloader

# Large PriMock demo WAVs are uploaded through Symfony before FastAPI replays them.
# Keep local dev upload limits above the checked-in fixture corpus.
RUN { \
    echo "upload_max_filesize=128M"; \
    echo "post_max_size=128M"; \
    echo "memory_limit=512M"; \
} > /usr/local/etc/php/conf.d/ambient-scribe-dev.ini

# The PHP built-in server listens on this port
EXPOSE 8080

# Start PHP's built-in web server, serving from the public/ directory
# The -t flag sets the document root (where index.php lives)
CMD ["php", "-S", "0.0.0.0:8080", "-t", "public"]
