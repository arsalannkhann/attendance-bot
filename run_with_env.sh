#!/bin/bash
# Loads .env and runs bot with given action
set -a
source "/Users/arsalan/Documents/attendance_bot/.env"
set +a
ZOHO_ACTION=$1 /usr/bin/python3 /Users/arsalan/Documents/attendance_bot/zoho_bot.py
