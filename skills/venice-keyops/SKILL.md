---
name: venice-keyops
description: Create, manage, rotate/cycle, monitor, and revoke Venice.ai API keys with spending budget caps, reset periods, and low-balance inference alerts.
---

# Venice KeyOps Agent Skill

Use this skill when the user asks to manage, create, list, rotate/cycle, or revoke Venice.ai API keys, check account balances or remaining inference budgets, configure spend limits, or run model inference tests.

## Available Tool Methods & CLI Commands

### 1. Check Account Balance & Rate Limits
Query USD/DIEM balance, access permission status, and epoch countdown:
```bash
venice-key-manager balance
```

### 2. List Active Keys & Remaining Budgets
List all keys with category and spend metrics:
```bash
venice-key-manager list
# Filter by category:
venice-key-manager list --category Agents
# Filter low-balance only:
venice-key-manager list --low-balance
```

### 3. Mint / Create a Dedicated Sub-Key
Create a key with custom daily/monthly budget ceiling:
```bash
venice-key-manager create --name <agent-name> --daily-usd <usd_limit> [--period EPOCH|MONTH|LIFETIME] [--category <cat>]
```
Example:
```bash
venice-key-manager create --name agent-researcher --daily-usd 0.50 --period EPOCH --category Agents
```

### 4. Rotate / Cycle a Key
Safely rotate an existing key (mints replacement with matching limits and revokes old key):
```bash
venice-key-manager cycle --id <key_id> [--daily-usd <new_usd>]
```

### 5. Revoke a Key
Permanently delete an API key:
```bash
venice-key-manager revoke --id <key_id>
```

### 6. Test Model Inference
Quickly benchmark latency and verify key functionality:
```bash
venice-key-manager test --prompt "Hello Venice" --model deepseek-v4-flash
```

### 7. Backup & Restore Settings
Export or restore categories and threshold settings:
```bash
venice-key-manager backup export --out venice-backup.json
venice-key-manager backup import --file venice-backup.json
```

## Model Context Protocol (MCP) Server
When configured as an MCP server, the following tools are available to the agent:
- `venice_list_keys`: Inspect active keys, consumption limits, and current period usage.
- `venice_create_key`: Mint dedicated keys with hard spend ceilings.
- `venice_cycle_key`: Rotate key and deprecate compromised or exhausted keys.
- `venice_update_key_limit`: Modify daily USD limits and category assignments.
- `venice_revoke_key`: Destroy API keys permanently.
- `venice_get_account_balance`: Retrieve live USD/DIEM balances and alert state.
- `venice_list_models`: Query confidential hardware enclaves (`e2ee`) and ZDR models.
- `venice_test_inference`: Verify model latency and response health.
