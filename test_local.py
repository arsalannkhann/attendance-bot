#!/usr/bin/env python3
"""Local test runner — loads .env and starts the server."""
import os

# Manually parse .env since special chars break shell sourcing
env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ[key.strip()] = value.strip()
    print("✅ Loaded .env")

os.environ.setdefault("PORT", "8080")

# Now import and run the server
from server import app
port = int(os.environ.get("PORT", 8080))
print(f"🚀 Starting server on http://localhost:{port}")
app.run(host="0.0.0.0", port=port, debug=True)
