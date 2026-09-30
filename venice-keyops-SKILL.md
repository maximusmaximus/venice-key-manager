---
name: venice-keyops
description: Create, manage, rotate/cycle, monitor, and revoke Venice.ai API keys with spending budget caps, reset periods, low-balance inference alerts, Cloudflare DNS gated inference gateway, model sizing tiers (xs to xl), project allocations, and delegated sub-keys ($0.25/day default).
---

# Venice KeyOps Agent Skill

Use this skill when the user asks to manage, create, list, rotate/cycle, or revoke Venice.ai API keys, check account balances or remaining inference budgets, configure spend limits, run model inference tests, or manage keyed gated inference projects, Cloudflare DNS gateway, model tiers (xs to xl), and delegated sub-keys.

## Available Tool Methods & CLI Commands

### 1. Check Account Balance & Rate Limits
Query USD/DIEM balance, access permission status, and epoch countdown:
```bash
/opt/fleet/bin/venice-key-manager balance
```

### 2. List Active Native Keys & Remaining Budgets
List all native keys with category and spend metrics:
```bash
/opt/fleet/bin/venice-key-manager list
# Filter by category:
/opt/fleet/bin/venice-key-manager list --category Agents
# Filter low-balance only:
/opt/fleet/bin/venice-key-manager list --low-balance
```

### 3. Mint / Create a Native Key
Create a key with custom daily/monthly budget ceiling. **Naming is mandatory**: the user must specify a name/label for the key. If omitted, prompt the user for a name before proceeding.
```bash
/opt/fleet/bin/venice-key-manager create --name <agent-name> --daily-usd <usd_limit> [--period EPOCH|MONTH|LIFETIME] [--category <cat>]
```

### 4. Rotate / Cycle a Key
Safely rotate an existing key (mints replacement with matching limits and revokes old key):
```bash
/opt/fleet/bin/venice-key-manager cycle --id <key_id> [--daily-usd <new_usd>]
```

### 5. Revoke a Key
Permanently delete an API key:
```bash
/opt/fleet/bin/venice-key-manager revoke --id <key_id>
```

### 6. Test Model Inference
Quickly benchmark latency and verify key functionality:
```bash
/opt/fleet/bin/venice-key-manager test --prompt "Hello Venice" --model deepseek-v4-flash
```

### 7. Backup & Restore Settings
Export or restore categories and threshold settings:
```bash
/opt/fleet/bin/venice-key-manager backup export --out venice-backup.json
/opt/fleet/bin/venice-key-manager backup import --file venice-backup.json
```

### 8. Web Dashboard Access & Magic Link
Generate a secure magic link and access key to unlock the Web Control Plane:
```bash
/opt/fleet/bin/venice-key-manager dashboard-link
```
Whenever mentioning localhost or providing the Web Dashboard URL, you MUST ALWAYS execute this command or output the full web link with the token parameter (e.g. `http://localhost:8660/?token=<token>`). Never mention plain `http://localhost:8660` alone.

### 9. Manage External Inference Projects
Group external services or agent swarms and set project-level spend ceilings. Project names are mandatory:
```bash
# List projects:
/opt/fleet/bin/venice-key-manager project list
# Create project (Name is required):
/opt/fleet/bin/venice-key-manager project create --name "Swarm Project" --daily-usd 3.00 --tier xl --sub-cap 0.25
# Update project allocation:
/opt/fleet/bin/venice-key-manager project update --id <proj_id> --daily-usd 5.00 --status active
# Delete project:
/opt/fleet/bin/venice-key-manager project delete --id <proj_id>
```

### 10. External Keys & Delegated Sub-Keys
Generate external keys for outside services and delegate child sub-keys.
**Rules**:
- **Mandatory Naming**: Keys and sub-keys must be named. If name is missing, prompt the user for one.
- **Default Rule**: By default unless created by admin, sub-keys are allocated **$0.25 USD per project per day**.
```bash
# List external keys:
/opt/fleet/bin/venice-key-manager extkey list [--project <proj_id>]

# Generate an external use key tied to a project (Name required):
/opt/fleet/bin/venice-key-manager extkey create --project <proj_id> --name "Hermes Client" --daily-usd 0.50 --tier m

# Delegate a child sub-key (Default: $0.25 USD per project per day unless created by admin):
/opt/fleet/bin/venice-key-manager extkey subkey --parent <token_or_id> --name "Worker Agent" [--amount 0.25] [--period DAY|WEEK]

# Modify external key:
/opt/fleet/bin/venice-key-manager extkey modify --id <key_id> --daily-usd 1.00 --tier xl --status active

# Revoke external key:
/opt/fleet/bin/venice-key-manager extkey revoke --id <key_id>
```

### 11. Dual Endpoint Delivery Protocol (CRITICAL)
Whenever providing, delivering, or outputting any API key, external key, or sub-key in chat, Telegram, or agent communications:
1. Deliver the key token cleanly in a code block.
2. In the **EXACT SAME MESSAGE**, you MUST deliver both network endpoints:
   - 🌐 **Tailscale Address (Internal Agents / Mesh)**: `http://100.99.202.75:8660/v1`
   - ☁️ **Cloudflare DNS URL (External Agents / Remote)**: `https://worship-him-knight-jul.trycloudflare.com/v1`
3. Provide ready-to-copy client snippets (cURL and OpenAI Python client) so receiving agents can connect immediately.

### 12. Cloudflare DNS Gateway & Model Sizing
Inspect or configure the public Cloudflare gateway endpoint:
```bash
/opt/fleet/bin/venice-key-manager gateway info
/opt/fleet/bin/venice-key-manager gateway set-url --url https://venice-gateway.yourdomain.com
```

#### Model Size Tiers:
- **`xs`**: Extra Small (3B, `llama-3.2-3b`, $0.05/$0.10 per 1M) — Ultra fast, routing, heartbeat.
- **`s`**: Small (Flash, `deepseek-v4-flash`, $0.15/$0.60 per 1M) — High speed agent loops.
- **`m`**: Medium (70B, `llama-3.3-70b`, $0.40/$1.20 per 1M) — Balanced tool calling, coding.
- **`l`**: Large (R1, `deepseek-r1`, $1.50/$4.00 per 1M) — Deep math & complex reasoning.
- **`xl`**: Extra Large (405B & E2EE enclaves, `llama-3.1-405b`, $3.00/$8.00 per 1M) — Frontier intelligence.

## Model Context Protocol (MCP) Server
When configured as an MCP server, the following tools are available:
- `venice_list_projects`, `venice_create_project`, `venice_update_project`
- `venice_list_external_keys`, `venice_create_external_key`, `venice_create_sub_key`, `venice_modify_external_key_allocation`, `venice_revoke_external_key`
- `venice_get_gateway_info`, `venice_set_cloudflare_gateway_url`, `venice_gateway_chat_completion`
- `venice_list_keys`, `venice_create_key`, `venice_cycle_key`, `venice_update_key_limit`, `venice_revoke_key`
- `venice_get_account_balance`, `venice_list_models`, `venice_test_inference`, `venice_daily_report`
