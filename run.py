#!/usr/bin/env python3
"""
Unified CLI & Service Runner for Venice.ai & Telegram Key Management Suite.

Usage:
  python run.py --all                 # Start Web Dashboard and Telegram Bot concurrently
  python run.py --web                 # Start Web Dashboard (default: http://localhost:8844)
  python run.py --telegram            # Start Telegram Bot Daemon
  python run.py --mcp                 # Start MCP Server in Stdio mode
  python run.py venice balance        # Check live account balances
  python run.py venice keys           # List keys
  python run.py venice infer "prompt" # Test light inference
  python run.py tg list               # List registered agent bots
  python run.py tg ping [agent]       # Send test ping from agent bot
"""

import sys
import io
import time
import argparse
import threading
from pathlib import Path

# Fix Windows console UTF-8 output
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.vault import KeyVault
from core.venice_client import VeniceClient
from core.tg_manager import TelegramAgentManager
from core.deployer import ConfigDeployer
from web.server import start_web_server
from telegram.bot import VeniceTelegramBot
from mcp.server import VeniceMCPServer
from core.tunnel import CloudflareTunnelManager


def run_all(port: int = 8844):
    print("=" * 60)
    print("⚡ VENICE & TELEGRAM // KEY MANAGEMENT & DEPLOYMENT SUITE")
    print("=" * 60)

    vault = KeyVault()
    tunnel_mgr = CloudflareTunnelManager(vault)
    cf_conf = vault.get_cloudflare_config()
    if cf_conf.get("domain") or cf_conf.get("tunnel_token"):
        print(f"[0/3] Connecting Cloudflare Tunnel ({cf_conf.get('domain', 'proxy')})...")
        tunnel_res = tunnel_mgr.start_tunnel(local_port=port, daemon=True)
        if tunnel_res.get("success"):
            print(f"      Connected! Public URL: {tunnel_res.get('public_url')}")
        else:
            print(f"      Notice: {tunnel_res.get('error')}")

    # 1. Start Web Server in background daemon thread
    print(f"[1/2] Launching Web Dashboard on http://localhost:{port} ...")
    start_web_server(port=port, daemon=True)

    # 2. Start Telegram Bot in main thread
    print("[2/2] Launching Telegram Bot Daemon (@songprocessor_bot) ...")
    bot = VeniceTelegramBot(vault=vault)
    try:
        bot.run()
    except KeyboardInterrupt:
        print("\n[!] Shutting down all services...")


def handle_cli_commands(args):
    vault = KeyVault()
    client = VeniceClient(
        admin_key=vault.get_venice_admin_key(),
        inference_key=vault.get_venice_inference_key(),
        base_url=vault.data.get("venice", {}).get("base_url", "")
    )
    tg_mgr = TelegramAgentManager(vault)

    if args.command == "venice":
        if args.subcommand == "balance":
            res = client.get_rate_limits()
            if res.get("success"):
                b = res.get("balances", {})
                t = res.get("apiTier", {})
                print(f"\n💰 Venice.ai Account Balances:")
                print(f"  USD Balance:      ${b.get('USD', 0):.2f}")
                print(f"  DIEM Token:       {b.get('DIEM', 0):.2f}")
                print(f"  Bundled Credits:  {b.get('BUNDLED_CREDITS', 0)}")
                print(f"  Subscription Tier: {t.get('id', 'paid').upper()}")
                print(f"  Next Reset Epoch:  {res.get('nextEpochBegins')}\n")
            else:
                print(f"\n❌ Error fetching balances: {res.get('error')}\n")

        elif args.subcommand == "keys":
            admin_key = vault.get_venice_admin_key()
            if not admin_key:
                print("\n⚠️ Venice Admin Key not set in vault. Showing local keys:")
                for k in vault.get_venice_keys():
                    print(f"  • {k.get('description')} ({k.get('apiKeyType')}) - ID: {k.get('id')}")
                print("\nConfigure master Admin Key to list remote keys via API.\n")
            else:
                res = client.list_keys(admin_key=admin_key)
                if res.get("success"):
                    print(f"\n🔑 Active Venice Keys ({res.get('count')}):")
                    for k in res.get("keys", []):
                        print(f"  • {k.get('description', 'Key')} [{k.get('apiKeyType')}] - ID: {k.get('id')}")
                    print()
                else:
                    print(f"\n❌ Error: {res.get('error')}\n")

        elif args.subcommand == "infer":
            prompt = args.prompt or "Say ping in 1 word"
            print(f"\n⚡ Running inference with prompt: '{prompt}'...")
            res = client.test_inference(prompt=prompt)
            if res.get("success"):
                cost = res.get("cost", {}).get("usd", 0)
                print(f"  Model:   {res.get('model')}")
                print(f"  Latency: {res.get('latency_ms')} ms")
                print(f"  Tokens:  {res.get('usage', {}).get('total_tokens', 0)}")
                print(f"  Cost:    ${cost:.6f} USD")
                print(f"\n  Output:\n  {res.get('content')}\n")
            else:
                print(f"❌ Inference failed: {res.get('error')}\n")

        elif args.subcommand == "deploy":
            key_str = args.key or vault.get_active_venice_key()
            print(f"🚀 Deploying Venice key to config...")
            res = ConfigDeployer.deploy_venice_key(key_str)
            if res.get("success"):
                print(f"  ✅ Deployed to: {res.get('target')}")
                print(f"  Backup saved to: {res.get('backup')}\n")
            else:
                print(f"  ❌ Deploy failed: {res.get('error')}\n")

    elif args.command == "tg":
        if args.subcommand == "list":
            bots = tg_mgr.list_agent_bots()
            print(f"\n🤖 Registered Agent Telegram Bots ({len(bots)}):")
            for b in bots:
                print(f"  • Agent:    {b.get('agent_name')}")
                print(f"    Username: @{b.get('username')}")
                print(f"    Name:     {b.get('first_name')}")
                print(f"    Config:   {b.get('config_path')}")
                print()

        elif args.subcommand == "ping":
            agent = args.agent or "primary-agent"
            bot = vault.get_agent_bot(agent)
            if not bot:
                bots = vault.get_agent_bots()
                bot = list(bots.values())[0] if bots else None
                if not bot:
                    print(f"❌ No agent bots found in vault.")
                    return
                agent = bot.get("agent_name")
            print(f"📡 Sending test ping from @{bot.get('username')}...")
            res = tg_mgr.send_test_message(bot["bot_token"])
            if res.get("success"):
                print(f"  ✅ Ping delivered! Telegram Message ID: {res.get('message_id')}\n")
            else:
                print(f"  ❌ Ping failed: {res.get('error')}\n")

        elif args.subcommand == "wizard":
            agent = args.agent or "worker-agent"
            wiz = tg_mgr.generate_botfather_wizard(agent)
            print(f"\n🧙 @BotFather Creation Wizard for Agent '{agent}':")
            print(f"  Deep Link: {wiz.get('botfather_deep_link')}")
            for step in wiz.get("steps", []):
                print(f"  {step}")
            print()


def main():
    parser = argparse.ArgumentParser(description="Venice & Telegram Key Management Suite")
    parser.add_argument("--all", action="store_true", help="Run Web Dashboard & Telegram Bot concurrently")
    parser.add_argument("--web", action="store_true", help="Run Web Dashboard")
    parser.add_argument("--telegram", action="store_true", help="Run Telegram Bot Daemon")
    parser.add_argument("--mcp", action="store_true", help="Run MCP Stdio Server")
    parser.add_argument("--port", type=int, default=8844, help="Web dashboard port (default: 8844)")

    subparsers = parser.add_subparsers(dest="command", help="CLI Subcommands")

    # Venice commands
    venice_parser = subparsers.add_parser("venice", help="Venice.ai commands")
    venice_sub = venice_parser.add_subparsers(dest="subcommand")
    venice_sub.add_parser("balance", help="Check balances")
    venice_sub.add_parser("keys", help="List keys")
    inf_cmd = venice_sub.add_parser("infer", help="Run light inference")
    inf_cmd.add_argument("prompt", nargs="?", default="Explain quantum in 5 words", help="Prompt text")
    dep_cmd = venice_sub.add_parser("deploy", help="Deploy key to config file")
    dep_cmd.add_argument("key", nargs="?", help="Key string to deploy")

    # Telegram commands
    tg_parser = subparsers.add_parser("tg", help="Telegram agent bot commands")
    tg_sub = tg_parser.add_subparsers(dest="subcommand")
    tg_sub.add_parser("list", help="List registered agent bots")
    ping_cmd = tg_sub.add_parser("ping", help="Send test ping")
    ping_cmd.add_argument("agent", nargs="?", default="primary-agent", help="Agent name")
    wiz_cmd = tg_sub.add_parser("wizard", help="Show @BotFather wizard")
    wiz_cmd.add_argument("agent", nargs="?", default="worker-agent", help="Agent name")

    args = parser.parse_args()

    if args.mcp:
        server = VeniceMCPServer()
        server.run_stdio()
    elif args.web:
        start_web_server(port=args.port, daemon=False)
    elif args.telegram:
        bot = VeniceTelegramBot()
        bot.run()
    elif args.all:
        run_all(port=args.port)
    elif args.command:
        handle_cli_commands(args)
    else:
        # Default to printing balance and help
        print("\n⚡ VENICE & TELEGRAM KEY MANAGER")
        print("Run with --all to start the Web Dashboard and Telegram Bot.\n")
        handle_cli_commands(argparse.Namespace(command="venice", subcommand="balance"))
        parser.print_help()


if __name__ == "__main__":
    main()
