#!/bin/bash
#
# Multi-Agent Sandbox - VPS Setup Script
# Transforms a fresh Ubuntu VPS into an agent orchestration environment
#
# Usage: curl -fsSL https://raw.githubusercontent.com/your-repo/main/infra/vps/setup.sh | bash
#
set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Configuration
WORKSPACE_DIR="/workspace"
AGENT_USER="${AGENT_USER:-agent}"
PYTHON_VERSION="3.11"
NODE_VERSION="20"

echo ""
echo "=========================================="
echo "  Multi-Agent Sandbox - VPS Setup"
echo "=========================================="
echo ""

# Check if running as root
if [[ $EUID -ne 0 ]]; then
    log_error "This script must be run as root (use sudo)"
    exit 1
fi

# Detect OS
if ! grep -q "Ubuntu" /etc/os-release 2>/dev/null; then
    log_error "This script requires Ubuntu 22.04 or later"
    exit 1
fi

log_info "Starting VPS setup..."

# ============================================
# Phase 1: System Updates & Base Packages
# ============================================
log_info "Phase 1: System updates and base packages..."

apt-get update -qq
apt-get upgrade -y -qq

apt-get install -y -qq \
    build-essential \
    curl \
    wget \
    git \
    tmux \
    htop \
    jq \
    unzip \
    sqlite3 \
    ca-certificates \
    gnupg \
    lsb-release \
    software-properties-common

log_success "Base packages installed"

# ============================================
# Phase 2: Python Installation
# ============================================
log_info "Phase 2: Installing Python ${PYTHON_VERSION}..."

add-apt-repository -y ppa:deadsnakes/ppa
apt-get update -qq
apt-get install -y -qq \
    python${PYTHON_VERSION} \
    python${PYTHON_VERSION}-venv \
    python${PYTHON_VERSION}-dev \
    python3-pip

# Set as default python3
update-alternatives --install /usr/bin/python3 python3 /usr/bin/python${PYTHON_VERSION} 1

# Install uv for fast package management
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.cargo/bin:$PATH"

log_success "Python ${PYTHON_VERSION} installed"

# ============================================
# Phase 3: Node.js Installation (for Claude Code)
# ============================================
log_info "Phase 3: Installing Node.js ${NODE_VERSION}..."

curl -fsSL https://deb.nodesource.com/setup_${NODE_VERSION}.x | bash -
apt-get install -y -qq nodejs

log_success "Node.js ${NODE_VERSION} installed"

# ============================================
# Phase 4: Claude Code Installation
# ============================================
log_info "Phase 4: Installing Claude Code..."

npm install -g @anthropic-ai/claude-code

log_success "Claude Code installed"

# ============================================
# Phase 5: Create Agent User & Workspace
# ============================================
log_info "Phase 5: Setting up agent user and workspace..."

# Create agent user if doesn't exist
if ! id "${AGENT_USER}" &>/dev/null; then
    useradd -m -s /bin/bash "${AGENT_USER}"
    log_info "Created user: ${AGENT_USER}"
fi

# Create workspace directory
mkdir -p "${WORKSPACE_DIR}"
chown -R "${AGENT_USER}:${AGENT_USER}" "${WORKSPACE_DIR}"

# Create log directory
mkdir -p /var/log/agent-sandbox
chown -R "${AGENT_USER}:${AGENT_USER}" /var/log/agent-sandbox

# Create ELF memory directory
mkdir -p "/home/${AGENT_USER}/.claude/emergent-learning"
chown -R "${AGENT_USER}:${AGENT_USER}" "/home/${AGENT_USER}/.claude"

log_success "Workspace created at ${WORKSPACE_DIR}"

# ============================================
# Phase 6: Clone Multi-Agent Sandbox
# ============================================
log_info "Phase 6: Cloning multi-agent-sandbox..."

REPO_DIR="/opt/multi-agent-sandbox"
if [[ -d "${REPO_DIR}" ]]; then
    cd "${REPO_DIR}"
    git pull origin main
else
    git clone https://github.com/YOUR_USERNAME/multi-agent-sandbox.git "${REPO_DIR}"
fi

cd "${REPO_DIR}"

# Install Python dependencies
python3 -m pip install --upgrade pip
python3 -m pip install -r worker/requirements.txt
python3 -m pip install -r control_api/requirements.txt

log_success "Multi-agent-sandbox installed"

# ============================================
# Phase 7: Configure tmux
# ============================================
log_info "Phase 7: Configuring tmux..."

cat > "/home/${AGENT_USER}/.tmux.conf" << 'EOF'
# Multi-Agent Sandbox tmux configuration

# Enable mouse support
set -g mouse on

# Increase scrollback buffer
set -g history-limit 50000

# Start windows and panes at 1, not 0
set -g base-index 1
setw -g pane-base-index 1

# Status bar
set -g status-bg colour235
set -g status-fg white
set -g status-left '[#S] '
set -g status-right '#H | %Y-%m-%d %H:%M'
set -g status-left-length 30

# Pane borders
set -g pane-border-style fg=colour240
set -g pane-active-border-style fg=colour51

# Window status
setw -g window-status-format ' #I:#W '
setw -g window-status-current-format ' #I:#W '
setw -g window-status-current-style bg=colour51,fg=colour235

# Reload config
bind r source-file ~/.tmux.conf \; display "Config reloaded!"

# Split panes using | and -
bind | split-window -h
bind - split-window -v

# Easy pane switching
bind -n M-Left select-pane -L
bind -n M-Right select-pane -R
bind -n M-Up select-pane -U
bind -n M-Down select-pane -D
EOF

chown "${AGENT_USER}:${AGENT_USER}" "/home/${AGENT_USER}/.tmux.conf"

log_success "tmux configured"

# ============================================
# Phase 8: Create systemd services
# ============================================
log_info "Phase 8: Creating systemd services..."

# Orchestrator service
cat > /etc/systemd/system/agent-orchestrator.service << EOF
[Unit]
Description=Multi-Agent Sandbox Orchestrator
After=network.target

[Service]
Type=simple
User=${AGENT_USER}
WorkingDirectory=${REPO_DIR}
Environment="MODE=vps"
Environment="WORKSPACE_DIR=${WORKSPACE_DIR}"
Environment="TMUX_SESSION=agents"
ExecStart=/usr/bin/python3 -m uvicorn control_api.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# Agent watcher service (monitors agent health)
cat > /etc/systemd/system/agent-watcher.service << EOF
[Unit]
Description=Multi-Agent Sandbox Watcher
After=network.target agent-orchestrator.service

[Service]
Type=simple
User=${AGENT_USER}
WorkingDirectory=${REPO_DIR}
Environment="MODE=vps"
ExecStart=/usr/bin/python3 -m agent_farm.watcher
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload

log_success "Systemd services created"

# ============================================
# Phase 9: Environment file
# ============================================
log_info "Phase 9: Creating environment file..."

cat > "${REPO_DIR}/.env" << EOF
# Multi-Agent Sandbox Environment Configuration
# Generated by setup.sh on $(date)

# Mode: local, vps, or aws
MODE=vps

# Workspace directory for job repos
WORKSPACE_DIR=${WORKSPACE_DIR}

# tmux session name for agents
TMUX_SESSION=agents

# API Configuration
API_HOST=0.0.0.0
API_PORT=8000

# Anthropic API Key (required)
# ANTHROPIC_API_KEY=sk-ant-...

# GitHub Token (for private repos)
# GITHUB_TOKEN=ghp_...

# ELF Memory Path
ELF_MEMORY_PATH=/home/${AGENT_USER}/.claude/emergent-learning

# Logging
LOG_LEVEL=INFO
LOG_DIR=/var/log/agent-sandbox
EOF

chown "${AGENT_USER}:${AGENT_USER}" "${REPO_DIR}/.env"

log_success "Environment file created at ${REPO_DIR}/.env"

# ============================================
# Phase 10: Final setup
# ============================================
log_info "Phase 10: Final setup..."

# Add agent user to necessary groups
usermod -aG docker "${AGENT_USER}" 2>/dev/null || true

# Create initial tmux session
su - "${AGENT_USER}" -c "tmux new-session -d -s agents -n main 2>/dev/null || true"

log_success "Setup complete!"

echo ""
echo "=========================================="
echo "  Setup Complete!"
echo "=========================================="
echo ""
echo "Next steps:"
echo ""
echo "1. Edit the environment file:"
echo "   sudo nano ${REPO_DIR}/.env"
echo "   - Add your ANTHROPIC_API_KEY"
echo "   - Add your GITHUB_TOKEN (if using private repos)"
echo ""
echo "2. Start the orchestrator:"
echo "   sudo systemctl start agent-orchestrator"
echo "   sudo systemctl enable agent-orchestrator"
echo ""
echo "3. Access the API:"
echo "   curl http://localhost:8000/health"
echo ""
echo "4. View agent tmux session:"
echo "   sudo -u ${AGENT_USER} tmux attach -t agents"
echo ""
echo "5. Submit a test job:"
echo "   curl -X POST http://localhost:8000/jobs \\"
echo "     -H 'Content-Type: application/json' \\"
echo "     -d '{\"job_type\": \"echo\", \"payload\": {\"message\": \"Hello\"}}'"
echo ""
