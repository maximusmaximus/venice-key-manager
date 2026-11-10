"""
End-to-end unit & integration tests for Agent Inference Reporting, Scheduled
Telegram Updates, Keep-Alive Log Rotation, Web Report API, and MCP Tools.
"""

import json
import shutil
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.reporter import AgentInferenceReporter
from core.supervisor import ServiceSupervisor
from core.vault import KeyVault
from core.venice_client import VeniceClient
from mcp.server import VeniceMCPServer
from telegram.bot import VeniceTelegramBot
from web.server import DashboardRequestHandler, start_web_server

FAKE_ADMIN_KEY = "VENICE_ADMIN_KEY_MOCK_SECRET_999999"
FAKE_INF_KEY = "VENICE_INFERENCE_KEY_MOCK_SECRET_888888"
FAKE_BOT_TOKEN = "100000002:MOCK_BOT_TOKEN_UNIT_TEST_VALUE_XYZ"
TEST_CHAT_ID = "999888777"
TEST_PAIR_CODE = "VK-RPTTEST1"


class TestAgentInferenceReporter(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vkm_report_test_"))
        self.vault = KeyVault(vault_path=self.tmp / "venice_vault.json")
        self.vault.set_pairing_code(TEST_PAIR_CODE)
        self.vault.set_venice_inference_key(FAKE_INF_KEY)
        self.vault.set_agent_admin_key("v3n15PE_bot", FAKE_ADMIN_KEY)
        self.vault.data.setdefault("telegram", {})["master_bot_token"] = FAKE_BOT_TOKEN
        self.vault.set_authorized_chat_id(TEST_CHAT_ID)
        self.vault.register_agent_bot("v3n15PE_bot", {
            "username": "v3n15PE_bot",
            "bot_token": FAKE_BOT_TOKEN,
        })

        # Seed keys, subkeys, allocations, and local inference events
        self.vault.store_venice_key({
            "id": "key_hermes_1",
            "apiKey": FAKE_INF_KEY,
            "description": "hermes-music (S Tier) [Agent: hermes-music]",
            "agent": "hermes-music",
            "maxModelTier": "s",
            "apiKeyType": "INFERENCE",
            "consumptionLimits": {"usd": 5.0},
            "status": "ACTIVE",
        })
        self.vault.store_subkey({
            "id": "sub_a2a_1",
            "label": "A2A Worker Key",
            "assigned_agent": "a2a-node",
            "quality_tier": "m",
            "budget_usd": 2.5,
            "key_string": FAKE_INF_KEY,
        })
        alloc = self.vault.mint_allocation(
            label="Hermes Claim",
            target_agent="hermes-music",
            allocated_keys_count=3,
            quality_tier="s",
            budget_usd=1.0,
            api_key=FAKE_INF_KEY,
        )
        self.vault.claim_allocation(alloc["claim_token"], agent_id="hermes-music")

        self.vault.record_inference_event(
            agent="v3n15PE_bot",
            model="deepseek-v4-flash",
            tokens=240,
            cost_usd=0.0012,
            latency_ms=410,
            success=True,
            source="tg_ask",
        )
        self.vault.record_inference_event(
            agent="hermes-music",
            model="llama-3.3-70b",
            tokens=560,
            cost_usd=0.0045,
            latency_ms=620,
            success=True,
            source="web",
        )

    def tearDown(self):
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def _mock_client(self, usd_balance: float = 12.50) -> MagicMock:
        mc = MagicMock(spec=VeniceClient)
        mc.check_balance_status.return_value = {
            "success": True,
            "status": "HEALTHY",
            "is_low": False,
            "is_out": False,
            "usd": usd_balance,
            "diem": 42.0,
            "bundled_credits": 0.0,
            "api_tier": "paid",
            "badge": "🟢 HEALTHY",
            "warning": None,
            "recharge_url": "https://venice.ai/settings/api",
        }
        mc.get_rate_limits.return_value = {
            "success": True,
            "balances": {"USD": usd_balance, "DIEM": 42.0},
            "apiTier": {"id": "paid"},
            "accessPermitted": True,
            "nextEpochBegins": "2026-11-11T00:00:00Z",
        }
        mc.list_keys.return_value = {
            "success": True,
            "count": 2,
            "keys": [
                {
                    "id": "key_hermes_1",
                    "description": "hermes-music (S Tier) [Agent: hermes-music]",
                    "apiKeyType": "INFERENCE",
                    "last6Chars": "ABCDEF",
                    "consumptionLimits": {"usd": 5.0},
                    "currentPeriodUsage": {"usd": 0.42},
                    "usage": {"trailingSevenDays": {"usd": 1.85, "diem": 0.0}},
                    "lastUsedAt": "2026-11-10T08:00:00Z",
                },
                {
                    "id": "key_bot_main",
                    "description": "v3n15PE_bot Controller Key",
                    "apiKeyType": "INFERENCE",
                    "last6Chars": "123456",
                    "consumptionLimits": {"usd": 10.0},
                    "currentPeriodUsage": {"usd": 0.15},
                    "usage": {"trailingSevenDays": {"usd": 0.65, "diem": 0.0}},
                    "lastUsedAt": "2026-11-10T09:00:00Z",
                },
            ],
        }
        return mc

    def test_report_aggregates_agents_and_redacts_secrets(self):
        mc = self._mock_client(usd_balance=12.50)
        reporter = AgentInferenceReporter(vault=self.vault, client=mc)
        rpt = reporter.generate_report(include_service=False)

        self.assertTrue(rpt["success"])
        self.assertEqual(rpt["account"]["usd"], 12.50)
        self.assertIsNone(rpt["account"]["usd_delta_since_last_report"])
        self.assertAlmostEqual(rpt["summary"]["spend_7d_usd"], 2.50, places=4)
        self.assertEqual(rpt["summary"]["local_calls_24h"], 2)
        self.assertEqual(rpt["summary"]["local_tokens_24h"], 800)
        self.assertEqual(rpt["summary"]["allocations_claimed"], 1)

        by_agent = {a["agent"]: a for a in rpt["agents"]}
        self.assertIn("v3n15PE_bot", by_agent)
        self.assertIn("hermes-music", by_agent)
        self.assertIn("a2a-node", by_agent)

        self.assertAlmostEqual(by_agent["hermes-music"]["spend_7d_usd"], 1.85, places=4)
        self.assertEqual(by_agent["hermes-music"]["allocations_claimed"], 1)
        self.assertEqual(by_agent["hermes-music"]["local_calls_24h"], 1)
        self.assertIsNotNone(by_agent["v3n15PE_bot"]["guard"])

        # Ensure zero secrets appear in JSON, Telegram Markdown, or CLI output
        serialized = json.dumps(rpt)
        md = reporter.format_telegram_markdown(rpt)
        cli = reporter.format_cli_text(rpt)
        for blob in (serialized, md, cli):
            self.assertNotIn(FAKE_ADMIN_KEY, blob)
            self.assertNotIn(FAKE_INF_KEY, blob)
            self.assertNotIn(FAKE_BOT_TOKEN, blob)

        self.assertIn("VENICE AGENT INFERENCE REPORT", md)
        self.assertIn("hermes-music", md)
        self.assertIn("v3n15PE_bot", md)

    def test_delta_tracking_across_dispatched_snapshots(self):
        self.vault.record_report_dispatch(usd_balance=15.00, diem_balance=42.0, summary={"spend_7d_usd": 2.0})
        mc = self._mock_client(usd_balance=14.25)
        reporter = AgentInferenceReporter(vault=self.vault, client=mc)
        rpt = reporter.generate_report(include_service=False)

        self.assertAlmostEqual(rpt["account"]["usd_delta_since_last_report"], -0.75, places=4)
        self.assertAlmostEqual(rpt["account"]["spend_since_last_report_usd"], 0.75, places=4)
        md = reporter.format_telegram_markdown(rpt, scheduled=True)
        self.assertIn("-$0.7500 USD spent since last report", md)

    @patch("telegram.bot.requests.post")
    def test_scheduled_auto_report_and_commands_in_telegram_bot(self, mock_post):
        mock_post.return_value.json.return_value = {"ok": True, "result": {"message_id": 10}}
        bot = VeniceTelegramBot(vault=self.vault)
        bot.reporter = AgentInferenceReporter(vault=self.vault, client=self._mock_client(10.0))

        # 1. Initial scheduled check dispatches baseline report because last_report_at == 0
        sent = bot.check_and_send_scheduled_report()
        self.assertIsNotNone(sent)
        self.assertEqual(self.vault.get_report_settings()["reports_sent_count"], 1)

        # 2. Immediate second check does NOT re-send before interval_hours elapses
        mock_post.reset_mock()
        skipped = bot.check_and_send_scheduled_report()
        self.assertIsNone(skipped)
        mock_post.assert_not_called()

        # 3. Backdate last_report_at past 6h interval -> dispatches next scheduled report
        cfg = self.vault.get_report_settings()
        cfg["last_report_at"] = time.time() - (6.1 * 3600)
        self.vault.data["report_settings"] = cfg
        self.vault._save(force=True)
        sent_again = bot.check_and_send_scheduled_report()
        self.assertIsNotNone(sent_again)
        self.assertEqual(self.vault.get_report_settings()["reports_sent_count"], 2)

        # 4. /report_schedule command updates cadence and pauses/resumes
        bot.handle_message({"chat": {"id": TEST_CHAT_ID}, "text": "/report_schedule 12"})
        self.assertEqual(self.vault.get_report_settings()["interval_hours"], 12.0)

        bot.handle_message({"chat": {"id": TEST_CHAT_ID}, "text": "/report_schedule off"})
        self.assertFalse(self.vault.get_report_settings()["enabled"])

        bot.handle_message({"chat": {"id": TEST_CHAT_ID}, "text": "/report_schedule on"})
        self.assertTrue(self.vault.get_report_settings()["enabled"])

        # 5. Natural language report request triggers report
        mock_post.reset_mock()
        bot.handle_message({"chat": {"id": TEST_CHAT_ID}, "text": "give me an inference report on the agent"})
        texts = " ".join(str(c[1].get("json", {}).get("text", "")) for c in mock_post.call_args_list)
        self.assertIn("VENICE AGENT INFERENCE REPORT", texts)

    def test_bot_token_redacted_from_log_strings(self):
        bot = VeniceTelegramBot(vault=self.vault)
        raw_err = f"HTTPSConnectionPool(host='api.telegram.org', port=443): Max retries exceeded with url: /bot{FAKE_BOT_TOKEN}/getUpdates"
        scrubbed = bot._redact_log(raw_err)
        self.assertNotIn(FAKE_BOT_TOKEN, scrubbed)
        self.assertIn("<REDACTED", scrubbed)

    def test_mcp_report_and_schedule_tools(self):
        mcp = VeniceMCPServer(vault=self.vault)
        with patch.object(VeniceClient, "check_balance_status", return_value=self._mock_client().check_balance_status.return_value), \
             patch.object(VeniceClient, "get_rate_limits", return_value=self._mock_client().get_rate_limits.return_value), \
             patch.object(VeniceClient, "list_keys", return_value=self._mock_client().list_keys.return_value):
            res = mcp.execute_tool("venice_agent_usage_report", {"send_telegram": False})
            self.assertTrue(res["success"])
            self.assertIn("agents", res)
            self.assertNotIn(FAKE_ADMIN_KEY, json.dumps(res))

            sched_res = mcp.execute_tool("venice_configure_report_schedule", {"enabled": True, "interval_hours": 4})
            self.assertTrue(sched_res["success"])
            self.assertEqual(sched_res["schedule"]["interval_hours"], 4.0)

    def test_web_report_endpoints_and_supervisor_log_rotation(self):
        # Verify supervisor log rotation
        log_file = self.tmp / ".supervisor.log"
        log_file.write_bytes(b"x" * 1024)
        rotated = ServiceSupervisor._rotate_log_if_needed(log_file, max_bytes=512)
        self.assertTrue(rotated)
        self.assertTrue(Path(str(log_file) + ".1").exists())

        # Verify web API endpoints require pairing & serve redacted agent report
        old_vault = DashboardRequestHandler.vault
        httpd = start_web_server(host="127.0.0.1", port=0, daemon=True, vault=self.vault)
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        try:
            with patch.object(VeniceClient, "check_balance_status", return_value=self._mock_client().check_balance_status.return_value), \
                 patch.object(VeniceClient, "get_rate_limits", return_value=self._mock_client().get_rate_limits.return_value), \
                 patch.object(VeniceClient, "list_keys", return_value=self._mock_client().list_keys.return_value), \
                 patch("telegram.bot.requests.post") as mock_post:
                mock_post.return_value.json.return_value = {"ok": True, "result": {"message_id": 55}}

                # Unauthenticated -> 401
                req_unauth = urllib.request.Request(base + "/api/reports/agent_usage")
                with self.assertRaises(urllib.error.HTTPError) as ctx_err:
                    urllib.request.urlopen(req_unauth, timeout=10)
                self.assertEqual(ctx_err.exception.code, 401)

                # Authenticated GET /api/reports/agent_usage -> 200
                req_auth = urllib.request.Request(
                    base + "/api/reports/agent_usage",
                    headers={"X-Pairing-Code": TEST_PAIR_CODE},
                )
                with urllib.request.urlopen(req_auth, timeout=10) as resp:
                    self.assertEqual(resp.status, 200)
                    payload = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(payload["success"])
                self.assertNotIn(FAKE_INF_KEY, json.dumps(payload))
                self.assertNotIn(FAKE_ADMIN_KEY, json.dumps(payload))

                # Authenticated POST /api/reports/settings -> 200
                req_set = urllib.request.Request(
                    base + "/api/reports/settings",
                    data=json.dumps({"enabled": True, "interval_hours": 3}).encode("utf-8"),
                    method="POST",
                    headers={"Content-Type": "application/json", "X-Pairing-Code": TEST_PAIR_CODE},
                )
                with urllib.request.urlopen(req_set, timeout=10) as resp:
                    set_data = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(set_data["schedule"]["interval_hours"], 3.0)

                # Authenticated POST /api/reports/send -> 200
                req_send = urllib.request.Request(
                    base + "/api/reports/send",
                    data=b"{}",
                    method="POST",
                    headers={"Content-Type": "application/json", "X-Pairing-Code": TEST_PAIR_CODE},
                )
                with urllib.request.urlopen(req_send, timeout=10) as resp:
                    send_data = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(send_data["success"])
        finally:
            httpd.shutdown()
            httpd.server_close()
            DashboardRequestHandler.vault = old_vault

    def test_7_telegram_401_token_auto_recovery(self):
        """Stale master_bot_token self-heals on HTTP 401 using registered v3n15PE_bot token."""
        stale_token = "100000099:STALE_REVOKED_UNIT_TEST_TOKEN"
        valid_token = "100000100:VALID_AGENT_UNIT_TEST_TOKEN"
        self.vault.set_telegram_master_token(stale_token)
        self.vault.register_agent_bot("v3n15PE_bot", {
            "username": "v3n15PE_bot",
            "bot_token": valid_token,
        })
        bot = VeniceTelegramBot(vault=self.vault)
        self.assertEqual(bot.token, stale_token)

        post_calls = []

        def fake_post(url, json=None, timeout=20):
            post_calls.append(url)
            resp = MagicMock()
            if stale_token in url:
                resp.status_code = 401
                resp.json.return_value = {"ok": False, "error_code": 401, "description": "Unauthorized"}
            else:
                resp.status_code = 200
                resp.json.return_value = {"ok": True, "result": {"message_id": 42}}
            return resp

        def fake_get(url, timeout=10):
            resp = MagicMock()
            if valid_token in url:
                resp.status_code = 200
                resp.json.return_value = {"ok": True, "result": {"username": "v3n15PE_bot"}}
            else:
                resp.status_code = 401
                resp.json.return_value = {"ok": False, "error_code": 401}
            return resp

        with patch("telegram.bot.requests.post", side_effect=fake_post), \
             patch("telegram.bot.requests.get", side_effect=fake_get):
            res = bot.send_message(TEST_CHAT_ID, "Self-healing token test")
            self.assertTrue(res["ok"])
            self.assertEqual(bot.token, valid_token)
            self.assertEqual(self.vault.get_telegram_master_token(), valid_token)
            self.assertEqual(len(post_calls), 2)


if __name__ == "__main__":
    unittest.main()
