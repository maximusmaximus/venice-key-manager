"""
Unit and integration tests for FleetMeshManager (Tailscale multi-node federation & git sync).
"""

import sys
import json
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.vault import KeyVault
from core.mesh import FleetMeshManager, DEFAULT_FLEET_NODES


class MockUrlOpenResponse:
    def __init__(self, data_dict):
        self.data_dict = data_dict

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

    def read(self):
        return json.dumps(self.data_dict).encode("utf-8")


class TestFleetMeshManager(unittest.TestCase):
    def setUp(self):
        self.vault = MagicMock(spec=KeyVault)
        self.vault.data = {}
        self.vault.get_venice_admin_key.return_value = "mock_admin"
        self.vault.get_venice_inference_key.return_value = "mock_inf"
        self.mesh = FleetMeshManager(vault=self.vault)

    def test_default_nodes_configured(self):
        nodes = self.mesh.list_nodes(check_health=False)
        node_names = [n["name"] for n in nodes]
        self.assertIn("local", node_names)
        self.assertIn("mcmini", node_names)

    def test_get_version_info(self):
        ver = self.mesh.get_version_info()
        self.assertTrue(ver.get("success"))
        self.assertIn("commit", ver)
        self.assertIn("branch", ver)
        self.assertIn("repo_url", ver)

    def test_register_and_remove_node(self):
        reg = self.mesh.register_node(
            name="test-worker",
            base_url="http://100.100.100.100:8844",
            label="Worker Node 3"
        )
        self.assertTrue(reg.get("success"))

        node = self.mesh.get_node("test-worker")
        self.assertIsNotNone(node)
        self.assertEqual(node.get("label"), "Worker Node 3")

        # Now remove
        removed = self.mesh.remove_node("test-worker")
        self.assertTrue(removed)
        self.assertIsNone(self.mesh.get_node("test-worker"))

    @patch("urllib.request.urlopen")
    def test_check_node_health_success(self, mock_urlopen):
        mock_urlopen.return_value = MockUrlOpenResponse({
            "success": True,
            "commit": "2c8508b",
            "branch": "main"
        })

        health = self.mesh.check_node_health("mcmini")
        self.assertEqual(health["status"], "online")
        self.assertTrue(health["online"])
        self.assertIsNotNone(health["latency_ms"])
        self.assertEqual(health["version"]["commit"], "2c8508b")

    @patch("urllib.request.urlopen")
    def test_check_node_health_offline(self, mock_urlopen):
        import urllib.error
        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

        health = self.mesh.check_node_health("mcmini")
        self.assertEqual(health["status"], "offline")
        self.assertFalse(health["online"])
        self.assertIsNone(health["latency_ms"])

    @patch("urllib.request.urlopen")
    def test_forward_request_get(self, mock_urlopen):
        mock_urlopen.return_value = MockUrlOpenResponse({
            "success": True,
            "balances": {"USD": 5.0}
        })

        res = self.mesh.forward_request("mcmini", "/api/stats", method="GET")
        self.assertTrue(res.get("success"))
        self.assertEqual(res.get("balances", {}).get("USD"), 5.0)

    @patch("urllib.request.urlopen")
    def test_forward_request_post(self, mock_urlopen):
        mock_urlopen.return_value = MockUrlOpenResponse({
            "success": True,
            "target": "config.yaml"
        })

        res = self.mesh.forward_request("mcmini", "/api/deploy_key", method="POST", payload={"key_string": "test"})
        self.assertTrue(res.get("success"))
        self.assertEqual(res.get("target"), "config.yaml")


if __name__ == "__main__":
    unittest.main()
