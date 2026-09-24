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

    # Auth token management
    p_auth = subparsers.add_parser("auth", help="Manage dashboard access keys")
    auth_sub = p_auth.add_subparsers(dest="auth_action", required=True)
    p_auth_create = auth_sub.add_parser("create-token", help="Generate a new access key")
    p_auth_create.add_argument("--ttl", type=int, default=168, help="Token validity in hours (default: 168)")
    p_auth_create.add_argument("--user", default="cli_admin", help="User tag")

    auth_sub.add_parser("list-tokens", help="List active access tokens")
    p_auth_revoke = auth_sub.add_parser("revoke-token", help="Revoke an access token")
    p_auth_revoke.add_argument("token", help="Token string to revoke")

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
        client = VeniceClient()
        req = KeyCreateRequest(
            description=args.name,
            daily_usd=args.daily_usd,
            limitPeriod=args.period,
            apiKeyType=args.type,
            category=args.category,
            custom_threshold=args.threshold
        )
        res = asyncio.run(client.create_key(req))
        print("\n🎉 Venice Key Created Successfully!")
        print(f"• Name:        {res.description}")
        print(f"• ID:          {res.id}")
        print(f"• Category:    {res.category}")
        print(f"• Type:        {res.apiKeyType}")
        print(f"• Spend Limit: {f'${args.daily_usd:.2f}' if args.daily_usd is not None else 'Unlimited'} ({res.limitPeriod})")
        print(f"• API Token:   {res.apiKey}")
        print("\n⚠️ Store this token now. It will never be shown again.\n")

    elif args.command == "cycle":
        client = VeniceClient()
        req = KeyCycleRequest(
            id=args.id,
            revoke_old=not args.no_revoke,
            new_daily_usd=args.daily_usd,
            new_description=args.name
        )
        res, revoked = asyncio.run(client.cycle_key(req))
        print("\n🔄 Key Successfully Cycled / Rotated!")
        print(f"• New Key ID:     {res.id}")
        print(f"• Old Key ID:     {args.id} (Revoked: {revoked})")
        print(f"• New API Token:  {res.apiKey}\n")

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
        token = state_store.create_auth_token(created_by=args.user)
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
            t = state_store.create_auth_token(created_by=args.user, ttl_hours=args.ttl)
            print(f"✅ Generated access key: {t}")
        elif args.auth_action == "list-tokens":
            tokens = state_store.list_active_tokens()
            print(json.dumps(tokens, indent=2))
        elif args.auth_action == "revoke-token":
            ok = state_store.revoke_auth_token(args.token)
            print(f"{'✅ Revoked' if ok else '❌ Token not found'}")


if __name__ == "__main__":
    main()
