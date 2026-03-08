#!/bin/bash
# ============================================
# Setup cron jobs for Zoho Bot
# Run once: bash setup_cron.sh
# ============================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON=$(which python3)
BOT="$SCRIPT_DIR/zoho_bot.py"
ENV_LOADER="$SCRIPT_DIR/run_with_env.sh"

# Create wrapper script that loads .env before running
cat > "$ENV_LOADER" << EOF
#!/bin/bash
# Loads .env and runs bot with given action
set -a
source "$SCRIPT_DIR/.env"
set +a
ZOHO_ACTION=\$1 $PYTHON $BOT
EOF

chmod +x "$ENV_LOADER"

echo "📋 Current crontab:"
crontab -l 2>/dev/null || echo "(empty)"

# Build new cron lines (times in IST — macOS cron uses system timezone)
# Check-in at 2:00 PM IST daily, Check-out at 11:59 PM IST daily
CHECKIN_CRON="0 14 * * * $ENV_LOADER checkin >> $SCRIPT_DIR/cron.log 2>&1"
CHECKOUT_CRON="59 23 * * * $ENV_LOADER checkout >> $SCRIPT_DIR/cron.log 2>&1"

# Add to crontab (avoid duplicates)
(crontab -l 2>/dev/null | grep -v "zoho_bot\|run_with_env"; echo "$CHECKIN_CRON"; echo "$CHECKOUT_CRON") | crontab -

echo ""
echo "✅ Cron jobs added:"
echo "   Daily 2:00 PM IST  → Check In"
echo "   Daily 11:59 PM IST → Check Out"
echo ""
echo "📋 Updated crontab:"
crontab -l
