# ⚡ Venice.ai Key Manager & Telegram Agent Suite

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python: 3.7+](https://img.shields.io/badge/Python-3.7+-green.svg)](https://python.org)
[![Model Context Protocol](https://img.shields.io/badge/MCP-2024--11--05-blueviolet.svg)](https://modelcontextprotocol.io)

An autonomous key management, real-time telemetry, and deployment control plane for **Venice.ai API keys** and **Telegram Agent Bots** (`@BotFather` integration). Engineered with full **Model Context Protocol (MCP)** server capabilities, a cyber dark-mode web dashboard, interactive Telegram bot controller, and Podman / Docker container support.

Built directly against the official Venice.ai REST API specifications to provide granular governance over autonomous AI agent fleets, developer teams, and production integrations.

---

## 🏛️ Architecture Overview

```
                      +---------------------------------------+
                      |      Venice.ai API & Telegram API      |
                      +---------------------------------------+
                                          ^
                                          |
                      +---------------------------------------+
                      |       Unified Core Engine & Vault     |
                      |  - VeniceClient (Balances/Keys/Infer) |
                      |  - TelegramAgentManager (@BotFather)  |
                      |  - ConfigDeployer (YAML/.env sync)    |
                      |  - Local Vault (venice_vault.json)    |
                      +---------------------------------------+
                                          |
         +--------------------------------+--------------------------------+
         |                                |                                |
         v                                v                                v
+-------------------+            +-------------------+            +-------------------+
|   Web Dashboard   |            |   Telegram Bot    |            |    MCP Server     |
|   (Port 8844)     |            |  Inline Buttons   |            |  JSON-RPC Stdio   |
| Live Stats & Sandbox           | Light Inference   |            | For Antigravity   |
+-------------------+            +-------------------+            +-------------------+
```

---

## 🚀 Key Features

* **🔑 Venice.ai Key Lifecycle & Budget Governance**:
  * Distinguishes and supports both **`INFERENCE`** and **`ADMIN`** keys.
  * Issue programmatic keys with daily/monthly budget caps in USD and custom expiration dates.
  * Programmatic key revocation (`DELETE /api/v1/api_keys/{id}`) and 1-click rotation/cycling.
  * Real-time querying of account balances (USD, DIEM, bundled credits), subscription tier, and epoch countdown.
* **⚡ Light Inference Sandbox**:
  * Test prompts directly against Venice models (`deepseek-v4-flash`, `zai-org-glm-5-1`, `gemini-3-6-flash`, etc.).
  * Real-time token breakdown (prompt, completion, total), dollar cost, and latency (ms).
  * Expandable model reasoning / thinking inspection.
* **🤖 Telegram Agent Bot & `@BotFather` Manager**:
  * Manage dedicated bot tokens mapped to each respective agent or service.
  * Live token validation via Telegram's `getMe` API.
  * **Automated `@BotFather` Mode**: Communicates directly with `@BotFather` via MTProto client to automate bot creation without manual copy-pasting.
  * **Guided Wizard Mode**: Generates 1-click deep links to `@BotFather` with prepopulated commands for instant generation and linking.
  * Verification pings: Sends test pings to your Telegram chat to confirm connectivity.
* **🚀 Configuration Deployment**:
  * 1-click safe deployment of active Venice keys or bot tokens to target configuration files (e.g. `config.yaml` or `.env`).
  * Automatically creates timestamped `.bak` backups before modifying any files.
* **🔌 Full Model Context Protocol (MCP) Server**:
  * Exposes 12 native tools over JSON-RPC 2.0 Stdio transport for AI agents in Antigravity, Claude Desktop, or Cursor.
  * Zero heavy external dependencies; starts in milliseconds.
* **🐳 Container & Quadlet Ready**:
  * Includes `Dockerfile`, `docker-compose.yml`, and Podman Quadlet unit (`quadlets/venice-key-manager.container`) for headless fleet deployment.

---

## 📦 Quickstart

### 1. Installation
Clone the repository and install requirements:
```bash
git clone https://github.com/maximusmaximus/venice-key-manager.git
cd venice-key-manager
pip install -r requirements.txt
```

### 2. Configuration
Copy the template configuration file:
```bash
cp .env.example .env
```
Fill in your credentials or start the server directly; credentials can also be entered through the browser dashboard:
- `VENICE_API_KEY`: Your Venice.ai inference or admin key.
- `VENICE_ADMIN_KEY`: (Optional) Required for creating and deleting keys via API.
- `TELEGRAM_BOT_TOKEN`: (Optional) Your Telegram bot token.
- `TELEGRAM_CHAT_ID`: (Optional) Your private Telegram user chat ID for security lock.

### 3. Launching Services

#### Run Web Dashboard & Telegram Bot Concurrently:
```bash
python run.py --all
```
Open your browser to: **[http://localhost:8844](http://localhost:8844)**

#### Run Only the Web Dashboard:
```bash
python run.py --web --port 8844
```

#### Run Only the Telegram Bot Daemon:
```bash
python run.py --telegram
```

---

## 💻 CLI Commands

Run terminal operations directly using `run.py`:

```bash
# Check live Venice balance, tier, and next reset epoch
python run.py venice balance

# List active keys
python run.py venice keys

# Test light inference with latency and token metrics
python run.py venice infer "Explain quantum computing in 10 words"

# Deploy active Venice key to config file
python run.py venice deploy

# List registered agent Telegram bots
python run.py tg list

# Send verification ping from an agent bot
python run.py tg ping

# Generate @BotFather creation steps for a new agent
python run.py tg wizard worker-agent
```

---

## 🔌 Model Context Protocol (MCP) Integration

To connect this manager to **Antigravity**, **Claude Desktop**, or any MCP-compliant client, register the server in your MCP configuration file (`mcp_config.json`):

```json
{
  "mcpServers": {
    "venice-key-manager": {
      "command": "python",
      "args": [
        "/path/to/venice-key-manager/run.py",
        "--mcp"
      ],
      "env": {
        "PYTHONUNBUFFERED": "1"
      }
    }
  }
}
```

### Available MCP Tools:
| Tool Name | Description |
| :--- | :--- |
| `venice_get_balances_and_tier` | Real-time USD, DIEM, bundled credits, and tier info. |
| `venice_list_rate_limits` | Retrieves model RPM/TPM constraints and epoch reset timer. |
| `venice_list_keys` | Lists all active Venice API keys (requires admin key). |
| `venice_create_key` | Generates an INFERENCE or ADMIN key with optional budget cap. |
| `venice_revoke_key` | Destroys an API key on Venice.ai by ID. |
| `venice_deploy_key` | Deploys a key directly into an agent's configuration file. |
| `venice_test_inference` | Runs light inference against Venice models with latency/cost metrics. |
| `tg_list_agent_bots` | Lists all registered agent bot tokens and their statuses. |
| `tg_register_agent_bot` | Validates token via `getMe`, assigns it to an agent, and saves to vault. |
| `tg_deploy_agent_bot` | Deploys an agent's bot token to its assigned configuration file. |
| `tg_test_agent_bot` | Sends a verification test ping from an agent bot to your chat. |
| `tg_botfather_wizard` | Returns step-by-step guidance and deep links for creating a bot with `@BotFather`. |

---

## 🔒 Security & Privacy Guarantees

- **Zero Secret Leaks**: All local keys and credentials are stored strictly in `venice_vault.json`, which is excluded by `.gitignore` and never committed to version control.
- **Telegram Access Control**: The Telegram controller strictly checks `TELEGRAM_CHAT_ID`, rejecting any unapproved senders.
- **Atomic File Backups**: All configuration file edits create safe timestamped `.bak` backups before writing changes.

---

## 📜 License
MIT License. See [LICENSE](LICENSE) for details.
