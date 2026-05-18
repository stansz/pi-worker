#!/usr/bin/env bash
# pi-worker installer — bootstraps a fresh VPS
# curl -sL https://raw.githubusercontent.com/stansz/pi-worker/main/install.sh | bash

set -euo pipefail

PI_VERSION="0.73.1"
PI_WORKER_DIR="$HOME/pi-worker"

echo "=== pi-worker installer ==="
echo ""

# ── 1. Clone repo ──────────────────────────────────────────────
echo "[1/5] Cloning pi-worker..."
if [ -d "$PI_WORKER_DIR" ]; then
    cd "$PI_WORKER_DIR" && git pull origin main
else
    git clone https://github.com/stansz/pi-worker.git "$PI_WORKER_DIR"
fi

# ── 2. Install Node.js if missing ───────────────────────────────
echo "[2/5] Checking Node.js..."
if ! command -v node &>/dev/null; then
    echo "  Installing Node.js via nvm..."
    curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash
    export NVM_DIR="$HOME/.nvm"
    [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
    nvm install 22
fi
echo "  Node: $(node --version)"

# ── 3. Install Pi ───────────────────────────────────────────────
echo "[3/5] Installing Pi @ ${PI_VERSION}..."
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
npm install -g "@mariozechner/pi-coding-agent@${PI_VERSION}"
echo "  Pi: $(pi --version 2>&1 || echo 'installed')"

# ── 4. Set up systemd unit ──────────────────────────────────────
echo "[4/5] Installing systemd unit..."

# Detect Pi binary location
PI_BIN=$(which pi 2>/dev/null || echo "")
PI_DIR=$(dirname "$PI_BIN" 2>/dev/null || echo "")
if [ -z "$PI_BIN" ]; then
    echo "  ERROR: pi binary not found — did install fail?"
    exit 1
fi

# Write systemd unit with detected paths
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/pi-worker.service << UNIT
[Unit]
Description=Pi Worker — HTTP endpoint for one-shot Pi agent dispatch
After=network-online.target
Wants=network-online.target
StartLimitBurst=50
StartLimitIntervalSec=60

[Service]
Type=simple
ExecStart=/usr/bin/python3 $PI_WORKER_DIR/listener.py
Restart=always
RestartSec=2
Environment=PI_WORKER_API_KEY=changeme
Environment=PI_WORKER_DIR=$PI_WORKER_DIR
Environment=PI_WORKER_PORT=9090
Environment=PI_WORKER_TIMEOUT=90
Environment=PI_WORKER_LOG_LEVEL=INFO
Environment=HOME=$HOME
Environment=PATH=$PI_DIR:/usr/bin:/usr/local/bin
Environment=PI_BINARY=$PI_BIN

[Install]
WantedBy=default.target
UNIT

systemctl --user daemon-reload
echo "  Pi binary: $PI_BIN"
echo "  Done. Edit ~/.config/systemd/user/pi-worker.service to set PI_WORKER_API_KEY"

# ── 5. Final instructions ───────────────────────────────────────
echo ""
echo "=== pi-worker installed ==="
echo ""
echo "Next steps:"
echo "  1. Edit ~/pi-worker/projects.yaml — list your project paths"
echo "  2. Generate an API key:    openssl rand -hex 32"
echo "  3. Set it in the systemd unit:"
echo "     systemctl --user edit pi-worker"
echo "     (add Environment=PI_WORKER_API_KEY=your-key)"
echo "  4. Enable and start:"
echo "     systemctl --user enable --now pi-worker"
echo "  5. Test:"
echo "     curl -s http://localhost:9090/health"
echo "  6. Expose via Cloudflare Tunnel (Zero Trust dashboard)"
echo ""
echo "Done."
