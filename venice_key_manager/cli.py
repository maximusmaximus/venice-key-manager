import sys
import json
import asyncio
import argparse

from .config import config
from .client import VeniceClient
from .models import (
    KeyCreateRequest,
    KeyCycleRequest,
    KeyUpdateRequest,
)
from .state import state_store


def main():
    parser = argparse.ArgumentParser(
        prog="venice-key-manager",
        description="Venice.ai API Key Lifecycle, MCP Server, Web Dashboard & Telegram Bot"
    )
    subparsers = parser.add_subparsers(dest="command")

    # 1. Web Dashboard
    web_p = subparsers.add_parser("web", help="Start the reactive Web Dashboard")
    web_p.add_argument("--host", default=config.web_host, help="Host to bind (default: 0.0.0.0)")
    web_p.add_argument("--port", type=int, default=config.web_port, help="Port to bind (default: 8660)")
    web_p.add_argument("--reload", action="store_true", help="Enable auto-reload for development")

    # 2. MCP Server
    mcp_p = subparsers.add_parser("mcp", help="Run the Model Context Protocol (MCP) server")

    # 3. Telegram Bot
    bot_p = subparsers.add_parser("bot", help="Run the Telegram touch bot")

    # 4. List Keys
    list_p = subparsers.add_parser("list", help="List all API keys with spending metrics")
    list_p.add_argument("--category", "-c", help="Filter by category")
    list_p.add_argument("--low-balance", action="store_true", help="Only show keys under warning threshold")
    list_p.add_argument("--json", action="store_true", help="Output raw JSON")

    # 5. Create Key
    create_p = subparsers.add_parser("create", help="Mint a new API key")
    create_p.add_argument("--name", "-n", required=True, help="Description or agent name")
    create_p.add_argument("--daily-usd", "-u", type=float, default=None, help="Spend cap in USD")
    create_p.add_argument("--period", "-p", choices=["EPOCH", "MONTH", "LIFETIME"], default="EPOCH", help="Reset period")
    create_p.add_argument("--category", default="Default", help="Category group")
    create_p.add_argument("--type", choices=["INFERENCE", "ADMIN"], default="INFERENCE", help="Key type")
    create_p.add_argument("--threshold", type=float, default=None, help="Custom low-balance threshold")

    # 5b. Batch Create Keys
    create_batch_p = subparsers.add_parser("create-batch", help="Mint a batch of API keys with a common name prefix")
    create_batch_p.add_argument("--prefix", "-p", required=True, help="Prefix for key names (e.g. worker-, agent-)")
    create_batch_p.add_argument("--count", "-c", type=int, default=3, help="Number of keys to mint (1-25)")
    create_batch_p.add_argument("--daily-usd", "-u", type=float, default=0.50, help="Spend cap in USD per key")
    create_batch_p.add_argument("--category", default="Default", help="Category group")
    create_batch_p.add_argument("--type", choices=["INFERENCE", "ADMIN"], default="INFERENCE", help="Key type")
    create_batch_p.add_argument("--period", choices=["EPOCH", "MONTH", "LIFETIME"], default="EPOCH", help="Reset period")

    # 6. Cycle Key
    cycle_p = subparsers.add_parser("cycle", help="Rotate/cycle an existing API key")
    cycle_p.add_argument("--id", required=True, help="Key ID to rotate")
    cycle_p.add_argument("--no-revoke", action="store_true", help="Do not delete the old key")
    cycle_p.add_argument("--daily-usd", type=float, help="Optional new daily USD limit")
    cycle_p.add_argument("--name", help="Optional new description")

    # 7. Revoke Key
    revoke_p = subparsers.add_parser("revoke", help="Permanently revoke a key")
    revoke_p.add_argument("--id", required=True, help="Key ID to delete")

    # 8. Balance & Rates
    bal_p = subparsers.add_parser("balance", help="Check account balance and rate limits")

    # 9. Test Inference
    test_p = subparsers.add_parser("test", help="Run light test inference")
    test_p.add_argument("--prompt", default="Respond with 'Venice API verified' in 4 words", help="Prompt text")
    test_p.add_argument("--model", default="deepseek-v4-flash", help="Model ID")
    test_p.add_argument("--key", help="Specific API key to test")

    # 10. Backup
    backup_p = subparsers.add_parser("backup", help="Manage state & settings backups")
    backup_sub = backup_p.add_subparsers(dest="backup_action")
    export_b = backup_sub.add_parser("export", help="Export state backup to JSON file")
    export_b.add_argument("--out", "-o", default="venice-backup.json", help="Output file path")
    import_b = backup_sub.add_parser("import", help="Import state backup from JSON file")
    import_b.add_argument("--file", "-f", required=True, help="JSON backup file path")

    # 11. Daily Report
    report_p = subparsers.add_parser("report", help="Generate and send daily key usage report")
    report_p.add_argument("--send-tg", action="store_true", help="Dispatch report to Telegram chat")
    report_p.add_argument("--chat", help="Target Telegram chat ID")
    report_p.add_argument("--json", action="store_true", help="Output raw JSON data")

    # Dashboard link
    p_dash = subparsers.add_parser("dashboard-link", help="Generate Telegram-authenticated magic link and access key")
    p_dash.add_argument("--base-url", default=None, help="Base URL override (defaults to DASHBOARD_BASE_URL)")
    p_dash.add_argument("--user", default="controller_tg", help="User tag")
    p_dash.add_argument("--prefix", default="vkm_tg_", help="Optional token prefix")

    # Auth token management
    p_auth = subparsers.add_parser("auth", help="Manage dashboard access keys")
    auth_sub = p_auth.add_subparsers(dest="auth_action", required=True)
    p_auth_create = auth_sub.add_parser("create-token", help="Generate a new access key")
    p_auth_create.add_argument("--prefix", default="vkm_code_", help="Prefix for access key")
    p_auth_create.add_argument("--ttl", type=int, default=168, help="Token validity in hours (default: 168)")
    p_auth_create.add_argument("--user", default="cli_admin", help="User tag")

    p_auth_batch = auth_sub.add_parser("create-batch", help="Generate a batch of access/pairing codes with a prefix")
    p_auth_batch.add_argument("--prefix", "-p", default="vkm_code_", help="Prefix for generated codes (e.g. team-, vip-)")
    p_auth_batch.add_argument("--count", "-c", type=int, default=5, help="Number of codes to generate")
    p_auth_batch.add_argument("--ttl", type=int, default=168, help="Token validity in hours (default: 168)")
    p_auth_batch.add_argument("--user", default="cli_admin", help="User tag")
    p_auth_batch.add_argument("--notes", help="Optional notes or tag")

    p_auth_list = auth_sub.add_parser("list-tokens", help="List active access tokens")
    p_auth_list.add_argument("--prefix", help="Filter by prefix")
    p_auth_revoke = auth_sub.add_parser("revoke-token", help="Revoke an access token")
    p_auth_revoke.add_argument("token", help="Token string to revoke")

    # 12. Project Allocations
    p_proj = subparsers.add_parser("project", help="Manage external inference projects and allocations")
    proj_sub = p_proj.add_subparsers(dest="project_action", required=True)
    p_proj_list = proj_sub.add_parser("list", help="List all projects and live spend")
    p_proj_list.add_argument("--json", action="store_true", help="Output raw JSON")
    p_proj_create = proj_sub.add_parser("create", help="Create a new project allocation")
    p_proj_create.add_argument("--name", "-n", required=True, help="Project name")
    p_proj_create.add_argument("--desc", default="", help="Description")
    p_proj_create.add_argument("--daily-usd", "-u", type=float, default=1.00, help="Daily spend limit in USD")
    p_proj_create.add_argument("--weekly-usd", type=float, help="Weekly spend limit in USD")
    p_proj_create.add_argument("--sub-cap", type=float, default=0.25, help="Default sub-key daily cap in USD (default: 0.25)")
    p_proj_create.add_argument("--tier", choices=["xs", "s", "m", "l", "xl"], default="xl", help="Max model tier")
    p_proj_update = proj_sub.add_parser("update", help="Update project limits or status")
    p_proj_update.add_argument("--id", required=True, help="Project ID")
    p_proj_update.add_argument("--name", help="New project name")
    p_proj_update.add_argument("--daily-usd", type=float, help="New daily limit")
    p_proj_update.add_argument("--sub-cap", type=float, help="New default sub-key cap")
    p_proj_update.add_argument("--tier", choices=["xs", "s", "m", "l", "xl"], help="New max tier")
    p_proj_update.add_argument("--status", choices=["active", "paused"], help="Status")
    p_proj_delete = proj_sub.add_parser("delete", help="Delete a project and revoke its keys")
    p_proj_delete.add_argument("--id", required=True, help="Project ID to delete")

    # 13. External Keys & Sub-Keys
    p_ext = subparsers.add_parser("extkey", help="Manage external use keys and delegated sub-keys")
    ext_sub = p_ext.add_subparsers(dest="extkey_action", required=True)
    p_ext_list = ext_sub.add_parser("list", help="List external keys")
    p_ext_list.add_argument("--project", "-p", help="Filter by project ID")
    p_ext_list.add_argument("--json", action="store_true", help="Output raw JSON")
    p_ext_create = ext_sub.add_parser("create", help="Create an external key tied to a project")
    p_ext_create.add_argument("--project", "-p", required=True, help="Target project ID")
    p_ext_create.add_argument("--name", "-n", required=True, help="Agent or client name")
    p_ext_create.add_argument("--daily-usd", "-u", type=float, help="Daily spend limit (defaults to project sub-key default: 0.25)")
    p_ext_create.add_argument("--period", choices=["DAY", "WEEK"], default="DAY", help="Limit period")
    p_ext_create.add_argument("--tier", choices=["xs", "s", "m", "l", "xl"], default="xl", help="Max model tier")
    p_ext_create.add_argument("--prefix", default="vkm_ext_", help="Token prefix")
    p_ext_create.add_argument("--notes", default="", help="Notes or owner info")
    p_ext_subkey = ext_sub.add_parser("subkey", help="Delegate a sub-key from a parent key or pairing token")
    p_ext_subkey.add_argument("--parent", required=True, help="Parent external key token, ID, or pairing code")
    p_ext_subkey.add_argument("--name", "-n", required=True, help="Sub-key / sub-agent name")
    p_ext_subkey.add_argument("--amount", "-a", type=float, default=0.25, help="Allocated amount in USD (default: 0.25)")
    p_ext_subkey.add_argument("--period", choices=["DAY", "WEEK"], default="DAY", help="Period")
    p_ext_subkey.add_argument("--tier", choices=["xs", "s", "m", "l", "xl"], help="Max model tier")
    p_ext_subkey.add_argument("--notes", default="", help="Notes")
    p_ext_modify = ext_sub.add_parser("modify", help="Modify an external key allocation")
    p_ext_modify.add_argument("--id", required=True, help="External key ID")
    p_ext_modify.add_argument("--name", help="New name")
    p_ext_modify.add_argument("--daily-usd", type=float, help="New daily limit")
    p_ext_modify.add_argument("--tier", choices=["xs", "s", "m", "l", "xl"], help="New max tier")
    p_ext_modify.add_argument("--status", choices=["active", "paused", "revoked"], help="Status")
    p_ext_revoke = ext_sub.add_parser("revoke", help="Revoke an external key or sub-key")
    p_ext_revoke.add_argument("--id", required=True, help="External key ID or token string to revoke")

    # 14. Gateway Configuration
    p_gw = subparsers.add_parser("gateway", help="Cloudflare DNS Gateway status and configuration")
    gw_sub = p_gw.add_subparsers(dest="gateway_action", required=True)
    p_gw_info = gw_sub.add_parser("info", help="View Cloudflare gateway URL and tier mapping")
    p_gw_set = gw_sub.add_parser("set-url", help="Set the public Cloudflare gateway DNS URL")
    p_gw_set.add_argument("--url", required=True, help="Public URL (e.g. https://venice-gateway.yourdomain.com)")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    # Dispatch
    if args.command == "web":
        import uvicorn
        print(f"🚀 Starting Venice Key Manager Web Dashboard at http://{args.host}:{args.port}")
        uvicorn.run("venice_key_manager.web.app:app", host=args.host, port=args.port, reload=args.reload)

    elif args.command == "mcp":
        from .mcp_server import main as run_mcp
        run_mcp()

    elif args.command == "bot":
        from .bot.tg_bot import run_bot
        run_bot()

    elif args.command == "list":
        client = VeniceClient()
        keys = asyncio.run(client.list_keys(category_filter=args.category))
        if args.low_balance:
            keys = [k for k in keys if k.is_low_balance]

        if args.json:
            print(json.dumps([k.model_dump() for k in keys], indent=2))
        else:
            print("\n🔑 VENICE.AI PROVISIONED API KEYS")
            print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            print(f"📊 Fleet Inventory: {len(keys)} active keys\n")
            print(f"{'Key Description':<28} {'Category':<12} {'Limit':<12} {'Period':<8} {'Remaining':<14} {'Last 6'}")
            print("─" * 85)
            for k in keys:
                limit_str = f"${k.consumptionLimits.usd:.2f}" if (k.consumptionLimits and k.consumptionLimits.usd is not None) else "Unlimited"
                rem_str = f"${k.remaining_usd:.4f}" if k.remaining_usd is not None else "--"
                warn = " ⚠️" if k.is_low_balance else ""
                print(f"{k.description[:26]:<28} {k.category:<12} {limit_str:<12} {k.limitPeriod:<8} {rem_str + warn:<14} ...{k.last6Chars}")
            print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")

    elif args.command == "create":
        if not args.name or not args.name.strip():
            print("❌ Error: Key name is required and cannot be empty.", file=sys.stderr)
            sys.exit(1)
        client = VeniceClient()
        req = KeyCreateRequest(
            description=args.name.strip(),
            daily_usd=args.daily_usd,
            limitPeriod=args.period,
            apiKeyType=args.type,
            category=args.category,
            custom_threshold=args.threshold
        )
        res = asyncio.run(client.create_key(req))
        endpoints = state_store.get_network_endpoints()
        print("\n🎉 Venice Key Created Successfully!")
        print(f"• Name:        {res.description}")
        print(f"• ID:          {res.id}")
        print(f"• Category:    {res.category}")
        print(f"• Type:        {res.apiKeyType}")
        print(f"• Spend Limit: {f'${args.daily_usd:.2f}' if args.daily_usd is not None else 'Unlimited'} ({res.limitPeriod})")
        print(f"• API Token:   {res.apiKey}")
        print("\n🌐 CONNECTION ENDPOINTS FOR RECEIVING AGENTS:")
        print(f"  • External (Cloudflare DNS): {endpoints['cloudflare_v1_url']}")
        print(f"  • Internal (Tailscale VPN):  {endpoints['tailscale_v1_url']}")
        print(f"  • Local Loopback:            {endpoints['local_v1_url']}")
        print("\n⚠️ Store this token now. It will never be shown again.\n")

    elif args.command == "create-batch":
        if not args.prefix or not args.prefix.strip():
            print("❌ Error: Key name prefix is required and cannot be empty.", file=sys.stderr)
            sys.exit(1)
        client = VeniceClient()
        results = asyncio.run(client.create_batch_keys(
            prefix=args.prefix.strip(),
            count=args.count,
            daily_usd=args.daily_usd,
            category=args.category,
            api_key_type=args.type,
            limit_period=args.period,
        ))
        endpoints = state_store.get_network_endpoints()
        print(f"\n🎉 Successfully Minted Batch of {len(results)} Keys (Prefix: '{args.prefix}')")
        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        for idx, k in enumerate(results, 1):
            print(f"#{idx:02d} | Name: {k.description:<24} | ID: {k.id}")
            print(f"     Token: {k.apiKey}")
        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print("🌐 CONNECTION ENDPOINTS FOR RECEIVING AGENTS:")
        print(f"  • External (Cloudflare DNS): {endpoints['cloudflare_v1_url']}")
        print(f"  • Internal (Tailscale VPN):  {endpoints['tailscale_v1_url']}")
        print(f"  • Local Loopback:            {endpoints['local_v1_url']}")
        print("⚠️ Store these secret tokens now. They will never be shown again.\n")

    elif args.command == "cycle":
        client = VeniceClient()
        req = KeyCycleRequest(
            id=args.id,
            revoke_old=not args.no_revoke,
            new_daily_usd=args.daily_usd,
            new_description=args.name
        )
        res, revoked = asyncio.run(client.cycle_key(req))
        endpoints = state_store.get_network_endpoints()
        print("\n🔄 Key Successfully Cycled / Rotated!")
        print(f"• New Key ID:     {res.id}")
        print(f"• Old Key ID:     {args.id} (Revoked: {revoked})")
        print(f"• New API Token:  {res.apiKey}")
        print("\n🌐 CONNECTION ENDPOINTS FOR RECEIVING AGENTS:")
        print(f"  • External (Cloudflare DNS): {endpoints['cloudflare_v1_url']}")
        print(f"  • Internal (Tailscale VPN):  {endpoints['tailscale_v1_url']}")
        print(f"  • Local Loopback:            {endpoints['local_v1_url']}\n")

    elif args.command == "revoke":
        client = VeniceClient()
        ok = asyncio.run(client.revoke_key(args.id))
        print(f"{'✅ Revoked' if ok else '❌ Failed to revoke'} key {args.id}")

    elif args.command == "balance":
        client = VeniceClient()
        rates = asyncio.run(client.get_rate_limits())
        thresh = state_store.get_global_threshold()
        is_low = rates.balances.USD <= thresh
        status_icon = "🟢" if rates.accessPermitted else "🔴"
        status_text = "Active & Permitted" if rates.accessPermitted else "Restricted"
        alert_flag = " ⚠️ [LOW BALANCE CRITICAL]" if is_low else ""

        print("\n📊 VENICE.AI TREASURY & ACCOUNT BALANCES")
        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print("💰 FINANCIAL HEALTH & BALANCES")
        print("─────────────────────────────────────────────────")
        print(f"  💵 Master USD Balance:        ${rates.balances.USD:.4f} USD{alert_flag}")
        print(f"  💎 DIEM Token Balance:        {rates.balances.DIEM:.4f} DIEM")
        print(f"  🎟️ Bundled Compute Credits:   {rates.balances.BUNDLED_CREDITS:.4f}")
        print(f"  {status_icon} API Access Status:         {status_text}")
        print(f"  ⏳ Rate-Limit Epoch Reset:    {rates.nextEpochBegins or '00:00 UTC'}")
        print(f"  🛡️ Global Warning Threshold:  ${thresh:.2f} USD")
        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")

    elif args.command == "test":
        client = VeniceClient()
        res = asyncio.run(client.test_inference(prompt=args.prompt, model=args.model, api_key=args.key))
        print("\n⚡ Venice Inference Test Result:")
        print(f"• Success:     {res.success}")
        print(f"• Model:       {res.model}")
        print(f"• Latency:     {res.latency_ms} ms")
        print(f"• Tokens:      {res.total_tokens} (prompt: {res.prompt_tokens}, completion: {res.completion_tokens})")
        if res.success:
            print(f"• Output:\n{res.output}\n")
        else:
            print(f"• Error: {res.error}\n")

    elif args.command == "backup":
        if args.backup_action == "export":
            backup = state_store.export_backup()
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(backup.model_dump_json(indent=2))
            print(f"💾 Backup exported to {args.out}")
        elif args.backup_action == "import":
            with open(args.file, "r", encoding="utf-8") as f:
                data = json.load(f)
            ok = state_store.import_backup(data)
            print(f"{'✅ Restored' if ok else '❌ Failed to restore'} backup from {args.file}")

    elif args.command == "report":
        from .report import DailyKeyReport
        reporter = DailyKeyReport()
        data = asyncio.run(reporter.generate_report_data())
        if args.json:
            print(json.dumps(data, indent=2))
        else:
            md = reporter.format_markdown(data)
            print("\n" + md + "\n")
            if args.send_tg:
                sent = asyncio.run(reporter.send_to_telegram(chat_id=args.chat))
                print(f"[Telegram] Report dispatched: {'✅ Success' if sent else '❌ Failed'}")

    elif args.command == "dashboard-link":
        token = state_store.create_auth_token(created_by=args.user, prefix=args.prefix)
        base = (args.base_url or config.dashboard_base_url).rstrip("/")
        magic_url = f"{base}/?token={token}"
        print("\n🌐 VENICE CONTROL PLANE — DASHBOARD ACCESS")
        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print("  🔗 Single-Click Magic Link: " + magic_url)
        print(f"  🔑 Telegram Access Key:     {token}")
        print("  ⏱️ Session Validity:        7 days")
        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")

    elif args.command == "auth":
        if args.auth_action == "create-token":
            t = state_store.create_auth_token(created_by=args.user, ttl_hours=args.ttl, prefix=args.prefix)
            print(f"✅ Generated access key: {t}")
        elif args.auth_action == "create-batch":
            tokens = state_store.create_batch_auth_tokens(
                prefix=args.prefix,
                count=args.count,
                created_by=args.user,
                ttl_hours=args.ttl,
                notes=args.notes
            )
            base = config.dashboard_base_url.rstrip("/")
            print(f"\n🎟️ Generated Batch of {len(tokens)} Access Codes (Prefix: '{args.prefix}')")
            print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            for idx, t in enumerate(tokens, 1):
                magic = f"{base}/?token={t['token']}"
                print(f"#{idx:02d} | Code: {t['token']}")
                print(f"     Link: {magic}")
            print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")
        elif args.auth_action == "list-tokens":
            tokens = state_store.list_active_tokens(prefix=args.prefix)
            print(json.dumps(tokens, indent=2))
        elif args.auth_action == "revoke-token":
            ok = state_store.revoke_auth_token(args.token)
            print(f"{'✅ Revoked' if ok else '❌ Token not found'}")

    elif args.command == "project":
        if args.project_action == "list":
            projects = state_store.list_projects()
            if args.json:
                print(json.dumps(projects, indent=2))
            else:
                print("\n📁 VENICE INFERENCE ALLOCATION PROJECTS")
                print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                print(f"{'Project Name':<28} {'Daily Limit':<14} {'Sub-Key Cap':<14} {'Max Tier':<10} {'Today Spend':<14} {'Keys'}")
                print("─" * 90)
                for p in projects:
                    d_lim = f"${p['daily_limit_usd']:.2f}"
                    sub_lim = f"${p.get('default_sub_key_daily_usd', 0.25):.2f}"
                    tier = p.get('max_model_tier', 'xl').upper()
                    spent = f"${p.get('current_day_spend', 0.0):.4f}"
                    keys_cnt = p.get('connected_keys_count', 0)
                    print(f"{p['name'][:26]:<28} {d_lim:<14} {sub_lim:<14} {tier:<10} {spent:<14} {keys_cnt}")
                print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")

        elif args.project_action == "create":
            p = state_store.create_project(
                name=args.name,
                description=args.desc,
                daily_limit_usd=args.daily_usd,
                weekly_limit_usd=args.weekly_usd,
                default_sub_key_daily_usd=args.sub_cap,
                max_model_tier=args.tier,
            )
            print(f"\n🎉 Project Created: {p['name']} (ID: {p['id']})")
            print(f"• Daily Spend Limit:   ${p['daily_limit_usd']:.2f} USD")
            print(f"• Default Sub-Key Cap: ${p['default_sub_key_daily_usd']:.2f} USD / day")
            print(f"• Max Permitted Tier:  {p['max_model_tier'].upper()}\n")

        elif args.project_action == "update":
            updates = {}
            if args.name: updates["name"] = args.name
            if args.daily_usd is not None: updates["daily_limit_usd"] = args.daily_usd
            if args.sub_cap is not None: updates["default_sub_key_daily_usd"] = args.sub_cap
            if args.tier: updates["max_model_tier"] = args.tier
            if args.status: updates["status"] = args.status
            res = state_store.update_project(args.id, **updates)
            if res:
                print(f"✅ Project '{args.id}' updated successfully.")
            else:
                print(f"❌ Project '{args.id}' not found.")

        elif args.project_action == "delete":
            ok = state_store.delete_project(args.id)
            print(f"{'✅ Project deleted' if ok else '❌ Cannot delete project (not found or default)'}")

    elif args.command == "extkey":
        if args.extkey_action == "list":
            keys = state_store.list_external_keys(project_id=args.project)
            if args.json:
                print(json.dumps(keys, indent=2))
            else:
                print("\n🔑 EXTERNAL KEYS & DELEGATED SUB-KEYS")
                print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                print(f"{'Key Name':<24} {'Project':<20} {'Type':<12} {'Tier':<6} {'Daily Cap':<12} {'Spent Today':<14} {'Token Last 6'}")
                print("─" * 100)
                for k in keys:
                    d_lim = f"${k.get('daily_limit_usd', 0.25):.2f}"
                    spent = f"${k.get('current_period_spend', 0.0):.4f}"
                    tier = k.get('max_model_tier', 'xl').upper()
                    last6 = k.get('token', '')[-6:] if k.get('token') else '--'
                    p_name = k.get('project_name', k.get('project_id', ''))[:18]
                    print(f"{k['name'][:22]:<24} {p_name:<20} {k.get('key_type', 'EXT'):<12} {tier:<6} {d_lim:<12} {spent:<14} ...{last6}")
                print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")

        elif args.extkey_action == "create":
            if not args.name or not args.name.strip():
                print("❌ Error: External key / agent name is required and cannot be empty.", file=sys.stderr)
                sys.exit(1)
            k = state_store.create_external_key(
                project_id=args.project,
                name=args.name.strip(),
                daily_limit_usd=args.daily_usd,
                limit_period=args.period,
                max_model_tier=args.tier,
                prefix=args.prefix,
                notes=args.notes,
            )
            endpoints = state_store.get_network_endpoints()
            print(f"\n🎉 External Key Created: {k['name']} (ID: {k['id']})")
            print(f"• Project ID:        {k['project_id']}")
            print(f"• Daily Spend Limit: ${k['daily_limit_usd']:.2f} USD")
            print(f"• Max Permitted Tier:{k['max_model_tier'].upper()}")
            print(f"• Key Token:         {k['token']}")
            print("\n🌐 CONNECTION ENDPOINTS FOR RECEIVING AGENTS:")
            print(f"  • External (Cloudflare DNS): {endpoints['cloudflare_v1_url']}")
            print(f"  • Internal (Tailscale VPN):  {endpoints['tailscale_v1_url']}")
            print(f"  • Local Loopback:            {endpoints['local_v1_url']}")
            print(f"\n⚡ cURL Test Snippets:")
            print(f"  [External Agent via Cloudflare]")
            print(f"  curl -X POST {endpoints['cloudflare_v1_url']}/chat/completions \\\n    -H 'Authorization: Bearer {k['token']}' \\\n    -H 'Content-Type: application/json' \\\n    -d '{{\"model\": \"{k['max_model_tier']}\", \"messages\": [{{\"role\": \"user\", \"content\": \"Hello!\"}}]}}'")
            print(f"  [Internal Agent via Tailscale]")
            print(f"  curl -X POST {endpoints['tailscale_v1_url']}/chat/completions \\\n    -H 'Authorization: Bearer {k['token']}' \\\n    -H 'Content-Type: application/json' \\\n    -d '{{\"model\": \"{k['max_model_tier']}\", \"messages\": [{{\"role\": \"user\", \"content\": \"Hello!\"}}]}}'\n")

        elif args.extkey_action == "subkey":
            if not args.name or not args.name.strip():
                print("❌ Error: Sub-key name is required and cannot be empty.", file=sys.stderr)
                sys.exit(1)
            sub = state_store.create_sub_key(
                parent_key_or_token=args.parent,
                name=args.name.strip(),
                amount_usd=args.amount,
                period=args.period,
                max_model_tier=args.tier,
                notes=args.notes,
            )
            endpoints = state_store.get_network_endpoints()
            print(f"\n🌱 Delegated Sub-Key Created: {sub['name']} (ID: {sub['id']})")
            print(f"• Parent Key ID:     {sub.get('parent_key_id')}")
            print(f"• Allocation Limit:  ${sub['daily_limit_usd']:.2f} USD / {sub['limit_period']}")
            print(f"• Max Model Tier:    {sub['max_model_tier'].upper()}")
            print(f"• Sub-Key Token:     {sub['token']}")
            print("\n🌐 CONNECTION ENDPOINTS FOR RECEIVING AGENTS:")
            print(f"  • External (Cloudflare DNS): {endpoints['cloudflare_v1_url']}")
            print(f"  • Internal (Tailscale VPN):  {endpoints['tailscale_v1_url']}")
            print(f"  • Local Loopback:            {endpoints['local_v1_url']}")
            print(f"\n⚡ cURL Test Snippets:")
            print(f"  [External Agent via Cloudflare]")
            print(f"  curl -X POST {endpoints['cloudflare_v1_url']}/chat/completions \\\n    -H 'Authorization: Bearer {sub['token']}' \\\n    -H 'Content-Type: application/json' \\\n    -d '{{\"model\": \"{sub['max_model_tier']}\", \"messages\": [{{\"role\": \"user\", \"content\": \"Hello!\"}}]}}'")
            print(f"  [Internal Agent via Tailscale]")
            print(f"  curl -X POST {endpoints['tailscale_v1_url']}/chat/completions \\\n    -H 'Authorization: Bearer {sub['token']}' \\\n    -H 'Content-Type: application/json' \\\n    -d '{{\"model\": \"{sub['max_model_tier']}\", \"messages\": [{{\"role\": \"user\", \"content\": \"Hello!\"}}]}}'\n")

        elif args.extkey_action == "modify":
            updates = {}
            if args.name: updates["name"] = args.name
            if args.daily_usd is not None: updates["daily_limit_usd"] = args.daily_usd
            if args.tier: updates["max_model_tier"] = args.tier
            if args.status: updates["status"] = args.status
            res = state_store.update_external_key(args.id, **updates)
            print(f"{'✅ External key updated' if res else '❌ Key not found'}")

        elif args.extkey_action == "revoke":
            ok = state_store.revoke_external_key(args.id)
            print(f"{'✅ External key revoked' if ok else '❌ Key not found'}")

    elif args.command == "gateway":
        if args.gateway_action == "info":
            from .models import MODEL_TIER_MAPPING, MODEL_TIER_ORDER
            gw_url = state_store.get_cloudflare_gateway_url() or "http://localhost:8660"
            print("\n🌐 CLOUDFLARE DNS GATEWAY STATUS")
            print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            print(f"  Public Cloudflare URL:  {state_store.get_cloudflare_gateway_url() or '(not configured, defaults to host)'}")
            print(f"  Effective Gateway URL:  {gw_url}")
            print(f"  Total Projects:         {len(state_store.list_projects())}")
            print(f"  Total External Keys:    {len(state_store.list_external_keys())}")
            print("\n  MODEL SIZING TIERS (xs to xl):")
            for t in MODEL_TIER_ORDER:
                info = MODEL_TIER_MAPPING[t]
                print(f"  • {t.upper():<3} : {info['default_model']:<18} ({info['label']})")
            print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")
        elif args.gateway_action == "set-url":
            state_store.set_cloudflare_gateway_url(args.url)
            print(f"✅ Cloudflare Gateway URL set to: {state_store.get_cloudflare_gateway_url()}")


if __name__ == "__main__":
    main()
