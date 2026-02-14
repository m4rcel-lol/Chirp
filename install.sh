#!/bin/sh
# One-command installation for Chirp

echo "🐦 Chirp Installation"
echo "===================="

# Check for Docker
if ! command -v docker >/dev/null 2>&1; then
    echo "Installing Docker..."
    apk add docker docker-compose 2>/dev/null || {
        echo "Please install Docker manually: https://docs.docker.com/get-docker/"
        exit 1
    }
fi

# Generate .env from example
if [ ! -f .env ]; then
    cp .env.example .env
    SECRET=$(openssl rand -hex 32 2>/dev/null || python3 -c "import secrets; print(secrets.token_hex(32))")
    sed -i "s/^SECRET_KEY=.*/SECRET_KEY=$SECRET/" .env
    echo "Generated .env with secret key"
fi

# Prompt for domain
printf "Enter your domain (e.g., chirp.example.com) [localhost]: "
read DOMAIN
DOMAIN=${DOMAIN:-localhost}
sed -i "s/^DOMAIN=.*/DOMAIN=$DOMAIN/" .env
sed -i "s|^BASE_URL=.*|BASE_URL=https://$DOMAIN|" .env

# Create required directories
mkdir -p app/database uploads

# Start services
docker-compose up -d --build

echo ""
echo "✅ Installation complete!"
echo "Visit http://$DOMAIN to access your Chirp instance"
echo "First visit /setup to create your admin account"
