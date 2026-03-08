#!/bin/bash
# ============================================
# Zoho Bot Setup Script for Mac
# Run once: bash setup.sh
# ============================================

set -e

echo "🚀 Setting up Zoho Attendance Bot..."

# 1. Check Python
echo "✅ Python: $(python3 --version)"

# 2. Install Python packages
echo "📦 Installing dependencies..."
pip3 install playwright imapclient pyzmail36 --break-system-packages

# 3. Install Playwright browser
echo "🌐 Installing Chromium browser..."
python3 -m playwright install chromium

# 4. Create .env file if not exists
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "📝 .env file created. Fill in your credentials!"
fi

echo ""
echo "✅ Setup complete!"
echo ""
echo "👉 Next steps:"
echo "   1. Edit .env with your credentials"
echo "   2. Run: python3 zoho_bot.py"
echo "   3. After first run, setup cron: bash setup_cron.sh"
