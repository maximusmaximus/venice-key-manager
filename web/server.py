"""
Web Server for Venice.ai & Telegram Key Management Dashboard.
Provides a multi-threaded HTTP server, the REST API and the responsive web app
(``web/app``). The original dashboard is still available under ``/legacy/``.

Security model
--------------
* Guests (no pairing code) can only reach the public endpoints: key validation,
  pairing, claim-portal / allocation inspect+claim and basic health/version.
* Everything else under ``/api/`` requires the machine pairing code supplied via
  ``X-Pairing-Code``, ``Authorization: Bearer <code>``, ``?pairing_code=`` or a
  ``pairing_code`` body field.
* Secrets (raw API keys, bot tokens) are redacted from list responses.
"""

import os
import sys
import json
import html as html_lib
import logging
import hashlib
import secrets
import threading
from datetime import datetime
from pathlib import Path
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from typing import Dict, Any, Optional, Tuple
from urllib.parse import urlsplit, parse_qs, unquote, urlencode

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.vault import KeyVault
from core.venice_client import VeniceClient
from core.tg_manager import TelegramAgentManager
from core.deployer import ConfigDeployer
from core.tiers import MODEL_TIER_ORDER, MODEL_TIER_MAPPING, is_tier_allowed, resolve_model_tier, get_model_for_tier
from core.mesh import FleetMeshManager
from core.tunnel import CloudflareTunnelManager
from core.supervisor import ServiceSupervisor

logger = logging.getLogger("venice_web")

WEB_DIR = Path(__file__).resolve().parent
APP_DIR = WEB_DIR / "app"          # new responsive web app
STATIC_DIR = WEB_DIR / "static"    # legacy dashboard (served at /legacy/)

MAX_BODY_BYTES = 1024 * 1024

MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".webmanifest": "application/manifest+json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".ico": "image/x-icon",
    ".txt": "text/plain; charset=utf-8",
    ".map": "application/json; charset=utf-8",
    ".woff2": "font/woff2",
}

# Strict CSP for the new app + claim portal (no inline script, no 3rd-party origins).
APP_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; font-src 'self'; manifest-src 'self'; object-src 'none'; "
    "base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
)

SECRET_FIELDS = ("apiKey", "api_key", "key_string", "bot_token", "secret")


class HTTPError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def mask_secret(value: Any) -> str:
    s = str(value or "")
    if not s:
        return ""
    if len(s) <= 12:
        return "***" + s[-2:]
    return f"{s[:6]}...{s[-4:]}"


def redact(obj: Any) -> Any:
    """Return a copy of ``obj`` with secret fields replaced by ``<field>_preview``."""
    if isinstance(obj, list):
        return [redact(x) for x in obj]
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in SECRET_FIELDS and isinstance(v, str) and v:
                out[f"{k}_preview"] = mask_secret(v)
                out[f"has_{k}"] = True
            else:
                out[k] = redact(v) if isinstance(v, (dict, list)) else v
        return out
    return obj


def _to_float(value: Any, default: float, name: str) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        raise HTTPError(f"'{name}' must be a number")


def _to_int(value: Any, default: int, name: str) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        raise HTTPError(f"'{name}' must be an integer")


def _safe_join(base: Path, rel: str) -> Optional[Path]:
    """Resolve ``rel`` inside ``base``; returns None on traversal or missing file."""
    try:
        base_r = base.resolve()
        candidate = (base_r / rel.lstrip("/\\")).resolve()
    except Exception:
        return None
    if candidate != base_r and base_r not in candidate.parents:
        return None
    if candidate.is_file():
        return candidate
    return None


class DashboardRequestHandler(BaseHTTPRequestHandler):
    """REST API + static file server.

    ``vault`` (and optionally ``tunnel_manager``) are class attributes so the
    process entrypoint can inject the shared instances; when left as ``None`` a
    vault is created lazily on first request.
    """

    vault = None  # type: Optional[KeyVault]
    tunnel_manager = None  # type: Optional[CloudflareTunnelManager]
    server_version = "VeniceKeyManager/1.3"
    protocol_version = "HTTP/1.0"

    _svc_lock = threading.Lock()
    _svc_cache = {}  # type: Dict[int, Dict[str, Any]]

    # ------------------------------------------------------------------ services
    def _ensure_vault(self) -> KeyVault:
        if self.vault is None:
            with DashboardRequestHandler._svc_lock:
                if DashboardRequestHandler.vault is None:
                    DashboardRequestHandler.vault = KeyVault()
        return self.vault

    def _services(self) -> Dict[str, Any]:
        v = self._ensure_vault()
        with DashboardRequestHandler._svc_lock:
            svc = DashboardRequestHandler._svc_cache.get(id(v))
            if svc is None or svc["vault"] is not v:
                tm = type(self).tunnel_manager
                if tm is None or getattr(tm, "vault", None) is not v:
                    tm = CloudflareTunnelManager(v)
                svc = {
                    "vault": v,
                    "tg": TelegramAgentManager(v),
                    "mesh": FleetMeshManager(v),
                    "tunnel": tm,
                    "mcp": None,
                }
                DashboardRequestHandler._svc_cache[id(v)] = svc
        return svc

    @property
    def tg_manager(self) -> TelegramAgentManager:
        return self._services()["tg"]

    @property
    def mesh(self) -> FleetMeshManager:
        return self._services()["mesh"]

    @property
    def tunnel(self) -> CloudflareTunnelManager:
        return self._services()["tunnel"]

    def _mcp(self):
        svc = self._services()
        if svc["mcp"] is None:
            from mcp.server import VeniceMCPServer
            svc["mcp"] = VeniceMCPServer(vault=svc["vault"])
        return svc["mcp"]

    def _get_venice_client(self) -> VeniceClient:
        v = self._ensure_vault()
        return VeniceClient(
            admin_key=v.get_venice_admin_key(),
            inference_key=v.get_venice_inference_key(),
            base_url=v.data.get("venice", {}).get("base_url", "")
        )

    def _supervisor(self) -> ServiceSupervisor:
        # root_dir=None so VENICE_SUPERVISOR_STATE_DIR (tests) is honoured.
        return ServiceSupervisor()

    # ------------------------------------------------------------------ output
    def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib signature
        logger.debug("%s - %s", self.address_string(), format % args)

    def _common_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Pairing-Code, Authorization")

    def _send_bytes(self, body: bytes, content_type: str, status: int = 200,
                    cache: str = "no-store", csp: Optional[str] = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        if csp:
            self.send_header("Content-Security-Policy", csp)
        self._common_headers()
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data, default=str).encode("utf-8")
        self._send_bytes(body, "application/json; charset=utf-8", status=status)

    def _send_html(self, html_str: str, status: int = 200, csp: Optional[str] = APP_CSP) -> None:
        self._send_bytes(html_str.encode("utf-8"), "text/html; charset=utf-8", status=status, csp=csp)

    def _send_file(self, path: Path, csp: Optional[str] = None) -> None:
        ctype = MIME_TYPES.get(path.suffix.lower(), "application/octet-stream")
        data = path.read_bytes()
        self._send_bytes(data, ctype, cache="no-cache", csp=csp)

    # ------------------------------------------------------------------ input
    def _parse_request(self) -> Tuple[str, Dict[str, str], Dict[str, list]]:
        parts = urlsplit(self.path)
        path = unquote(parts.path) or "/"
        raw_qs = parse_qs(parts.query, keep_blank_values=True)
        query = {k: (v[-1] if v else "") for k, v in raw_qs.items()}
        return path, query, raw_qs

    def _read_json_body(self) -> Dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except (TypeError, ValueError):
            raise HTTPError("Invalid Content-Length header")
        if length < 0:
            raise HTTPError("Invalid Content-Length header")
        if length > MAX_BODY_BYTES:
            raise HTTPError("Request body too large", status=413)
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        if not raw.strip():
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise HTTPError("Malformed JSON body")
        if not isinstance(data, dict):
            raise HTTPError("JSON body must be an object")
        return data

    def _is_authenticated(self, query: Dict[str, Any], body: Optional[Dict[str, Any]] = None) -> bool:
        vault = self.vault if self.vault is not None else self._ensure_vault()
        if not vault.is_pairing_required():
            return True
        header_code = (self.headers.get("X-Pairing-Code", "") or "").strip()
        if header_code and vault.verify_pairing_code(header_code):
            return True
        auth_hdr = (self.headers.get("Authorization", "") or "").strip()
        if auth_hdr.startswith("Bearer "):
            bearer_code = auth_hdr[7:].strip()
            if vault.verify_pairing_code(bearer_code):
                return True
        q_val = query.get("pairing_code", "")
        if isinstance(q_val, list):
            query_code = q_val[-1].strip() if q_val else ""
        else:
            query_code = str(q_val).strip()
        if query_code and vault.verify_pairing_code(query_code):
            return True
        if body:
            b_code = body.get("pairing_code") or body.get("code")
            if b_code and vault.verify_pairing_code(str(b_code).strip()):
                return True
        return False

    def _deny(self) -> None:
        self._send_json({
            "success": False,
            "requires_pairing": True,
            "error": "This machine is in Protected Mode. Please enter the secure pairing code to unlock."
        }, status=401)

    # ------------------------------------------------------------------ dispatch
    def _dispatch(self, handler) -> None:
        try:
            handler()
        except HTTPError as e:
            self._send_json({"success": False, "error": str(e)}, status=e.status)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception as e:  # pragma: no cover - defensive
            logger.exception("Unhandled error for %s %s", self.command, self.path)
            try:
                self._send_json({"success": False, "error": f"Internal server error: {e}"}, status=500)
            except Exception:
                pass

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self._common_headers()
        self.end_headers()

    def do_GET(self):
        self._dispatch(self._handle_get)

    def do_HEAD(self):
        self._dispatch(self._handle_get)

    def do_POST(self):
        self._dispatch(self._handle_post)

    # ------------------------------------------------------------------ static
    def _serve_static(self, path: str) -> None:
        if path in ("/", "/index.html"):
            target = _safe_join(APP_DIR, "index.html")
            if target:
                return self._send_file(target, csp=APP_CSP)
            target = _safe_join(STATIC_DIR, "index.html")
            if target:
                return self._send_file(target)
        elif path in ("/legacy", "/legacy/"):
            target = _safe_join(STATIC_DIR, "index.html")
            if target:
                return self._send_file(target)
        elif path.startswith("/legacy/"):
            target = _safe_join(STATIC_DIR, path[len("/legacy/"):])
            if target:
                return self._send_file(target)
        else:
            rel = path.lstrip("/")
            target = _safe_join(APP_DIR, rel)
            if target:
                return self._send_file(target, csp=APP_CSP if target.suffix == ".html" else None)
            target = _safe_join(STATIC_DIR, rel)
            if target:
                return self._send_file(target)
        self._send_json({"success": False, "error": "Not found"}, status=404)

    # ------------------------------------------------------------------ claim portal
    def _send_claim_portal_html(self, token: str) -> None:
        alloc = self.vault.inspect_allocation(token)
        esc = html_lib.escape

        head = (
            '<!DOCTYPE html>\n<html lang="en">\n<head>\n'
            '  <meta charset="UTF-8">\n'
            '  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
            '  <meta name="color-scheme" content="dark light">\n'
            '  <meta name="robots" content="noindex">\n'
            '  <title>{title}</title>\n'
            '  <link rel="icon" href="/assets/icon.svg" type="image/svg+xml">\n'
            '  <link rel="stylesheet" href="/assets/app.css">\n'
            '</head>\n'
        )

        if not alloc.get("success"):
            page = head.format(title="Venice // Invalid Key Allocation") + (
                '<body class="portal">\n<main class="portal-card" id="main">\n'
                '  <div class="portal-icon" aria-hidden="true">!</div>\n'
                '  <h1 class="portal-title">Allocation not found</h1>\n'
                '  <p class="muted">The allocation token <code class="mono">{token}</code> is invalid, expired, or has been revoked.</p>\n'
                '  <a class="btn btn-primary" href="/">Return to dashboard</a>\n'
                '</main>\n</body>\n</html>'
            ).format(token=esc(token or ""))
            self._send_html(page, status=404)
            return

        status = str(alloc.get("status", "ACTIVE"))
        can_claim = bool(alloc.get("can_claim_now", False))
        status_label = "Ready to claim" if can_claim else ("Pending window" if status == "PENDING" else status.title())
        status_cls = "ok" if can_claim else ("warn" if status == "PENDING" else "bad")
        try:
            budget = float(alloc.get("budget_usd", 0.25) or 0)
        except (TypeError, ValueError):
            budget = 0.0
        claim_url = str(alloc.get("claim_url") or f"https://venice.vmu.cash/claim/{token}")

        def stat(label: str, value: str, sub: str) -> str:
            return ('<div class="stat"><div class="stat-label">{}</div><div class="stat-value">{}</div>'
                    '<div class="stat-sub">{}</div></div>').format(esc(label), esc(value), esc(sub))

        stats = "".join([
            stat("Allocated keys", str(alloc.get("allocated_keys_count", 1)),
                 f"{alloc.get('remaining_claims', 0)} remaining"),
            stat("Model tier", f"Tier {str(alloc.get('quality_tier', 's')).upper()}", "Max quality cap"),
            stat("Budget cap", f"${budget:.2f}", f"Reset: {alloc.get('limit_period', 'DAY')}"),
        ])

        if can_claim:
            action = ('<button type="button" class="btn btn-primary btn-block" id="btn-claim">'
                      'Claim Allocated Key Now</button>')
        else:
            action = '<p class="notice">Cannot claim at this time ({}).</p>'.format(esc(status_label))

        page = head.format(title="Venice Key Allocation Portal") + (
            '<body class="portal">\n'
            '<main class="portal-card" id="claim-root" data-token="{token}" data-agent="{agent}">\n'
            '  <header class="portal-header">\n'
            '    <div>\n'
            '      <p class="eyebrow">Venice Key Allocation</p>\n'
            '      <h1 class="portal-title">{label}</h1>\n'
            '      <p class="muted mono">Target agent: <strong>{agent}</strong></p>\n'
            '    </div>\n'
            '    <span class="badge badge-{status_cls}">{status_label}</span>\n'
            '  </header>\n'
            '  <section class="stat-grid" aria-label="Allocation details">{stats}</section>\n'
            '  <dl class="kv">\n'
            '    <div><dt>Claiming opens</dt><dd class="mono">{valid_from}</dd></div>\n'
            '    <div><dt>Expires</dt><dd class="mono">{valid_until}</dd></div>\n'
            '  </dl>\n'
            '  <div class="field">\n'
            '    <div class="field-head"><span class="label">Claim link</span>'
            '<button type="button" class="btn btn-ghost btn-sm" data-copy="{claim_url}">Copy link</button></div>\n'
            '    <code class="code-box">{claim_url}</code>\n'
            '  </div>\n'
            '  <div id="claim-action-area">{action}</div>\n'
            '  <div id="claim-result" role="status" aria-live="polite"></div>\n'
            '  <footer class="portal-footer"><span class="muted">Venice Key Manager</span>'
            '<a href="/">Dashboard</a></footer>\n'
            '</main>\n'
            '<script src="/assets/js/claim.js" defer></script>\n'
            '</body>\n</html>'
        ).format(
            token=esc(token),
            agent=esc(str(alloc.get("target_agent", "Agent"))),
            label=esc(str(alloc.get("label", "Agent Key Allocation"))),
            status_cls=status_cls,
            status_label=esc(status_label),
            stats=stats,
            valid_from=esc(str(alloc.get("valid_from") or "Immediate")),
            valid_until=esc(str(alloc.get("valid_until") or "No expiration")),
            claim_url=esc(claim_url),
            action=action,
        )
        self._send_html(page, status=200)

    # ------------------------------------------------------------------ helpers
    def _balance_status(self, client: VeniceClient) -> Dict[str, Any]:
        thresholds = self.vault.get_balance_thresholds()
        st = client.check_balance_status(
            low_threshold_usd=thresholds.get("low_usd", 1.0),
            out_threshold_usd=thresholds.get("out_usd", 0.05)
        )
        st["thresholds"] = thresholds
        return st

    # ------------------------------------------------------------------ GET
    def _handle_get(self) -> None:
        self._ensure_vault()
        path, query, raw_qs = self._parse_request()

        # Peer node forwarding for GET requests
        target_node = query.get("node")
        if target_node and target_node != "local" and path.startswith("/api/"):
            if not self._is_authenticated(query):
                return self._deny()
            fwd_qs = {k: v for k, v in raw_qs.items() if k not in ("node", "pairing_code")}
            rel = path + (("?" + urlencode(fwd_qs, doseq=True)) if fwd_qs else "")
            return self._send_json(self.mesh.forward_request(target_node, rel, method="GET"))

        # 1. Public endpoints ---------------------------------------------------
        if path in ("/api/health", "/healthz"):
            return self._send_json({"success": True, "ok": True, "time": datetime.utcnow().isoformat() + "Z"})

        if path == "/api/version":
            return self._send_json(self.mesh.get_version_info())

        if path == "/api/pairing_status":
            is_paired = self._is_authenticated(query)
            domain = self.tunnel.get_configured_domain()
            return self._send_json({
                "success": True,
                "authenticated": is_paired,
                "paired": is_paired,
                "requires_pairing": self.vault.is_pairing_required(),
                "require_pairing": self.vault.is_pairing_required(),
                "domain": domain,
                "configured_domain": domain,
                "tunnel": self.tunnel.get_status()
            })

        if path == "/api/tunnel/status":
            return self._send_json(self.tunnel.get_status())

        if path == "/api/service/status":
            return self._send_json(self._supervisor().get_status())

        if path.startswith("/claim/"):
            token = path[len("/claim/"):].strip("/")
            if query.get("format") == "json" or "application/json" in (self.headers.get("Accept", "") or ""):
                res = self.vault.inspect_allocation(token)
                return self._send_json(res, status=200 if res.get("success") else 404)
            return self._send_claim_portal_html(token)

        if path == "/api/allocations/inspect":
            token_or_url = query.get("token") or query.get("url") or query.get("claim_token") or ""
            res = self.vault.inspect_allocation(token_or_url)
            return self._send_json(res, status=200 if res.get("success") else 404)

        # 2. Static files (no auth – the app itself contains no data) ------------
        if not path.startswith("/api/"):
            return self._serve_static(path)

        # 3. Pairing gate for all other /api/* endpoints -------------------------
        if not self._is_authenticated(query):
            return self._deny()

        client = self._get_venice_client()

        if path == "/api/overview":
            bal = self._balance_status(client)
            sup = self._supervisor().get_status()
            allocs = self.vault.get_allocations(include_secret=False)
            return self._send_json({
                "success": True,
                "balance": bal,
                "service": sup,
                "tunnel": self.tunnel.get_status(),
                "version": self.mesh.get_version_info(),
                "vault": self.vault.get_backup_status(),
                "counts": {
                    "keys": len(self.vault.get_venice_keys()),
                    "subkeys": len(self.vault.get_subkeys()),
                    "allocations": len(allocs),
                    "allocations_active": len([a for a in allocs if a.get("status") == "ACTIVE"]),
                    "agent_bots": len(self.vault.get_agent_bots()),
                    "fleet_nodes": len(self.mesh.list_nodes(check_health=False)),
                },
                "has_admin_key": bool(self.vault.get_venice_admin_key()),
                "has_inference_key": bool(self.vault.get_venice_inference_key()),
                "domain": self.tunnel.get_configured_domain(),
            })

        if path == "/api/balance":
            bal = self._balance_status(client)
            bal["last_alert"] = self.vault.get_last_balance_alert_state()
            return self._send_json(bal)

        if path == "/api/pairing/code":
            return self._send_json({
                "success": True,
                "pairing_code": self.vault.get_pairing_code(),
                "requires_pairing": self.vault.is_pairing_required(),
                "env_override": bool(os.environ.get("SECURE_PAIRING_CODE")),
            })

        if path == "/api/mcp/tools":
            tools = self._mcp().get_tool_definitions()
            return self._send_json({"success": True, "total": len(tools), "tools": tools})

        if path == "/api/fleet/nodes":
            probe = query.get("probe") in ("1", "true")
            return self._send_json({"success": True, "nodes": self.mesh.list_nodes(check_health=probe)})

        if path == "/api/allocations":
            target_agent = query.get("target_agent") or None
            return self._send_json({
                "success": True,
                "allocations": self.vault.get_allocations(include_secret=False, target_agent=target_agent)
            })

        if path == "/api/subkeys":
            return self._send_json({"success": True, "subkeys": redact(self.vault.get_subkeys())})

        if path == "/api/stats":
            rate_res = client.get_rate_limits()
            balances = rate_res.get("balances", {}) if rate_res.get("success") else {"USD": 0, "DIEM": 0}
            tier = rate_res.get("apiTier", {}) if rate_res.get("success") else {}
            next_epoch = rate_res.get("nextEpochBegins") if rate_res.get("success") else None
            bal_status = self._balance_status(client)
            return self._send_json({
                "success": True,
                "balances": balances,
                "balance_status": bal_status.get("status", "HEALTHY"),
                "badge": bal_status.get("badge", "HEALTHY"),
                "is_low": bal_status.get("is_low", False),
                "is_out": bal_status.get("is_out", False),
                "warning": bal_status.get("warning"),
                "recharge_url": bal_status.get("recharge_url", "https://venice.ai/settings/api"),
                "apiTier": tier,
                "nextEpochBegins": next_epoch,
                "has_admin_key": bool(self.vault.get_venice_admin_key()),
                "has_inference_key": bool(self.vault.get_venice_inference_key()),
                "keys_count": len(self.vault.get_venice_keys()),
                "agent_bots_count": len(self.vault.get_agent_bots())
            })

        if path == "/api/keys":
            admin_key = self.vault.get_venice_admin_key()
            remote_res = client.list_keys(admin_key=admin_key) if admin_key else {"keys": []}
            merged = {}
            for k in self.vault.get_venice_keys():
                merged[k.get("id")] = dict(k, source="vault")
            for k in remote_res.get("keys", []) or []:
                merged[k.get("id")] = {**merged.get(k.get("id"), {}), **k}
            return self._send_json({
                "success": True,
                "requires_admin": remote_res.get("requires_admin", not bool(admin_key)),
                "keys": redact(list(merged.values()))
            })

        if path == "/api/rate_limits":
            res = client.get_rate_limits()
            if res.get("success"):
                raw = res.get("raw", {})
                return self._send_json({
                    "success": True,
                    "rateLimits": raw.get("rateLimits", []),
                    "apiTier": raw.get("apiTier", {}),
                    "nextEpochBegins": raw.get("nextEpochBegins"),
                    "balances": res.get("balances", {})
                })
            return self._send_json(res, status=400)

        if path == "/api/models":
            return self._send_json(client.list_models())

        if path == "/api/model_tiers":
            return self._send_json({"success": True, "tier_order": MODEL_TIER_ORDER, "tiers": MODEL_TIER_MAPPING})

        if path == "/api/agent_bots":
            bots = self.tg_manager.list_agent_bots(check_live_status=query.get("probe") in ("1", "true"))
            return self._send_json({"success": True, "agent_bots": redact(bots)})

        if path == "/api/config":
            admin = self.vault.get_venice_admin_key()
            inf = self.vault.get_venice_inference_key()
            return self._send_json({
                "success": True,
                "admin_key_set": bool(admin),
                "admin_key_preview": f"***{admin[-4:]}" if admin else "",
                "inference_key_set": bool(inf),
                "inference_key_preview": f"***{inf[-4:]}" if inf else "",
                "authorized_chat_id": self.vault.get_authorized_chat_id(),
                "base_url": self.vault.data.get("venice", {}).get("base_url"),
                "cloudflare_domain": self.tunnel.get_configured_domain(),
                "balance_thresholds": self.vault.get_balance_thresholds(),
            })

        if path == "/api/vault/status":
            return self._send_json(self.vault.get_backup_status())

        if path == "/api/vault/backups":
            return self._send_json({"success": True, "backups": self.vault.list_backups()})

        return self._send_json({"success": False, "error": "Endpoint not found"}, status=404)

    # ------------------------------------------------------------------ POST
    def _handle_post(self) -> None:
        self._ensure_vault()
        path, query, _raw_qs = self._parse_request()
        body = self._read_json_body()

        # Peer node forwarding for POST requests (privileged)
        target_node = body.get("target_node")
        if target_node and target_node != "local" and path not in ("/api/allocations/mint", "/api/subkeys/create"):
            if not self._is_authenticated(query, body):
                return self._deny()
            payload = {k: v for k, v in body.items() if k not in ("target_node", "pairing_code")}
            return self._send_json(self.mesh.forward_request(target_node, path, method="POST", payload=payload))

        client = self._get_venice_client()

        # 1. Public endpoints ---------------------------------------------------
        if path == "/api/pair":
            code = str(body.get("code") or body.get("pairing_code") or "").strip()
            if not code:
                return self._send_json({"success": False, "authenticated": False, "paired": False,
                                        "error": "Missing pairing code."}, status=400)
            if self.vault.verify_pairing_code(code):
                return self._send_json({
                    "success": True,
                    "authenticated": True,
                    "paired": True,
                    "pairing_code": code,
                    "message": "Secure code verified. Machine paired successfully!"
                })
            return self._send_json({
                "success": False,
                "authenticated": False,
                "paired": False,
                "error": "Invalid pairing code. Please enter the correct code configured on this machine."
            }, status=401)

        if path == "/api/validate_key":
            api_key = str(body.get("api_key") or body.get("key") or body.get("key_string") or "").strip()
            if not api_key:
                return self._send_json({"success": False, "valid": False, "error": "API Key cannot be empty."}, status=400)

            rate_res = client.get_rate_limits(key=api_key)
            if not rate_res.get("success"):
                err_msg = rate_res.get("error", "Key verification failed")
                if rate_res.get("status_code") == 401:
                    err_msg = "Venice rejected this API key (401 Unauthorized). Please check your key string."
                elif rate_res.get("status_code") == 402:
                    err_msg = "Key is valid but the account has no inference credits (402 Insufficient Balance)."
                return self._send_json({
                    "success": False,
                    "valid": False,
                    "error": err_msg,
                    "status_code": rate_res.get("status_code", 401)
                }, status=200)

            balances = rate_res.get("balances", {}) or {}
            small_test = client.test_inference(
                prompt="Ping from Venice Validator.",
                model="deepseek-v4-flash",
                max_tokens=15,
                key=api_key
            )
            try:
                usd = float(balances.get("USD", 0) or 0)
                diem = float(balances.get("DIEM", 0) or 0)
            except (TypeError, ValueError):
                usd, diem = 0.0, 0.0
            return self._send_json({
                "success": True,
                "valid": True,
                "masked_key": mask_secret(api_key),
                "balances": balances,
                "apiTier": rate_res.get("apiTier", {}),
                "nextEpochBegins": rate_res.get("nextEpochBegins"),
                "small_test_passed": small_test.get("success", False),
                "small_test_error": None if small_test.get("success") else small_test.get("error"),
                "latency_ms": small_test.get("latency_ms", 0),
                "message": f"Valid Venice Key! Live balance: ${usd:.2f} USD ({diem:.2f} DIEM)."
            })

        if path == "/api/allocations/claim":
            token_or_url = str(body.get("claim_token") or body.get("claim_url") or body.get("token") or "").strip()
            agent_id = str(body.get("agent_id") or "").strip()
            claim_res = self.vault.claim_allocation(
                token_or_url,
                agent_id=agent_id,
                client_info=f"Web Claim {self.client_address[0]}"
            )
            if not claim_res.get("success"):
                return self._send_json(claim_res, status=400)

            # Auto-deploy writes into local agent configs -> only for paired callers.
            if body.get("auto_deploy") and self._is_authenticated(query, body):
                key_str = claim_res.get("api_key")
                ag_name = agent_id or claim_res.get("target_agent")
                tp = body.get("target_path")
                claim_res["deployment"] = ConfigDeployer.deploy_venice_key(
                    key_str, target_path=Path(tp) if tp else None, agent_name=ag_name
                )
            return self._send_json(claim_res, status=200)

        # 2. Pairing gate ---------------------------------------------------------
        if not self._is_authenticated(query, body):
            return self._deny()

        # 3. Privileged endpoints -------------------------------------------------
        if path == "/api/subkeys/create":
            parent_key_id = body.get("parent_key_id", "")
            name = str(body.get("name") or body.get("label") or "Sub-Key Agent").strip()
            budget = _to_float(body.get("budget_usd"), 0.25, "budget_usd")
            period = str(body.get("limit_period") or body.get("period") or "DAY").upper()
            max_tier = str(body.get("max_model_tier") or body.get("quality_tier") or "s").lower().strip()
            if max_tier not in MODEL_TIER_ORDER:
                raise HTTPError(f"Unknown model tier '{max_tier}'. Use one of: {', '.join(MODEL_TIER_ORDER)}")
            target_agent = str(body.get("target_agent") or body.get("assigned_agent") or "").strip()

            keys = self.vault.get_venice_keys()
            parent_key = next((k for k in keys if k.get("id") == parent_key_id), None) if parent_key_id else None
            if not parent_key and keys:
                parent_key = keys[0]
            raw_key_val = (parent_key.get("apiKey") if parent_key else None) or self.vault.get_active_venice_key()
            if not raw_key_val:
                raise HTTPError("No Venice key available in the vault to derive a sub-key from.")

            subkey_entry = {
                "id": f"vsk_{secrets.token_hex(5)}",
                "name": name,
                "label": name,
                "apiKey": raw_key_val,
                "parent_key_id": parent_key.get("id") if parent_key else "vault_default",
                "budget_usd": budget,
                "limit_period": period,
                "period": period,
                "maxModelTier": max_tier,
                "quality_tier": max_tier,
                "target_agent": target_agent,
                "assigned_agent": target_agent,
                "assigned_node": body.get("target_node") or body.get("assigned_node") or "local",
                "created_at": datetime.utcnow().isoformat() + "Z",
                "status": "ACTIVE"
            }
            self.vault.store_subkey(subkey_entry)

            deployed_to = None
            if target_agent and body.get("deploy", True):
                dep_res = ConfigDeployer.deploy_venice_key(raw_key_val, agent_name=target_agent)
                if dep_res.get("success"):
                    deployed_to = dep_res.get("target")

            return self._send_json({
                "success": True,
                "subkey": redact(subkey_entry),
                "deployed_to": deployed_to,
                "message": f"Sub-key '{name}' provisioned with ${budget:.2f}/{period.lower()} cap."
            })

        if path == "/api/subkeys/remove":
            sk_id = str(body.get("id") or body.get("subkey_id") or "").strip()
            if not sk_id:
                raise HTTPError("Missing sub-key id")
            removed = self.vault.remove_subkey(sk_id)
            return self._send_json({"success": removed, "id": sk_id,
                                    "error": None if removed else "Sub-key not found"},
                                   status=200 if removed else 404)

        if path == "/api/subkeys/apply":
            key_val = body.get("key_string") or body.get("api_key")
            subkey_id = body.get("subkey_id")
            agent_name = body.get("agent_name", "hermes-music")
            target_path = body.get("target_path")

            if not key_val and subkey_id:
                sk = next((s for s in self.vault.get_subkeys() if s.get("id") == subkey_id), None)
                if sk:
                    key_val = sk.get("apiKey")
            if not key_val:
                key_val = self.vault.get_active_venice_key()

            dep_res = ConfigDeployer.deploy_venice_key(
                key_val,
                target_path=Path(target_path) if target_path else None,
                agent_name=agent_name
            )
            if dep_res.get("success") and subkey_id:
                for s in self.vault.get_subkeys():
                    if s.get("id") == subkey_id:
                        s["target_agent"] = agent_name
                        s["deployed_at"] = datetime.utcnow().isoformat() + "Z"
                        self.vault.store_subkey(s)
                        break
            return self._send_json(dep_res)

        if path == "/api/tunnel/start":
            port = _to_int(body.get("port"), 8844, "port")
            return self._send_json(self.tunnel.start_tunnel(local_port=port))

        if path == "/api/fleet/sync":
            node = body.get("node", "local")
            if node == "all":
                res = self.mesh.sync_all_nodes()
            elif node == "local":
                res = self.mesh.sync_local_code()
            else:
                res = self.mesh.sync_remote_node(node)
            return self._send_json(res)

        if path == "/api/fleet/register_node":
            name = str(body.get("name", "")).strip()
            base_url = str(body.get("base_url", "")).strip()
            if not name or not base_url:
                raise HTTPError("Both 'name' and 'base_url' are required")
            if not base_url.startswith(("http://", "https://")):
                raise HTTPError("'base_url' must start with http:// or https://")
            res = self.mesh.register_node(
                name, base_url,
                label=body.get("label", ""),
                ip=body.get("ip", ""),
                port=_to_int(body.get("port"), 8844, "port"),
                pairing_code=str(body.get("remote_pairing_code") or body.get("node_pairing_code") or "")
            )
            return self._send_json(res)

        if path == "/api/fleet/remove_node":
            name = body.get("name", "")
            removed = self.mesh.remove_node(name)
            return self._send_json({"success": removed, "name": name})

        if path == "/api/allocations/mint":
            label = str(body.get("label") or "").strip()
            target_agent = str(body.get("target_agent") or "").strip()
            count = _to_int(body.get("allocated_keys_count"), 1, "allocated_keys_count")
            if count < 1 or count > 1000:
                raise HTTPError("'allocated_keys_count' must be between 1 and 1000")
            quality_tier = str(body.get("quality_tier") or "s").strip().lower()
            if quality_tier not in MODEL_TIER_ORDER:
                raise HTTPError(f"Unknown model tier '{quality_tier}'")
            alloc = self.vault.mint_allocation(
                label=label,
                target_agent=target_agent,
                allocated_keys_count=count,
                valid_from=body.get("valid_from") or None,
                valid_until=body.get("valid_until") or None,
                quality_tier=quality_tier,
                budget_usd=_to_float(body.get("budget_usd"), 0.25, "budget_usd"),
                limit_period=str(body.get("limit_period") or "DAY").strip().upper(),
                target_node=str(body.get("target_node") or "local").strip(),
                api_key=body.get("api_key") or None
            )
            return self._send_json({
                "success": True,
                "allocation": redact(alloc),
                "claim_url": alloc["claim_url"],
                "claim_token": alloc["claim_token"],
                "message": f"Successfully minted allocation for '{alloc['target_agent']}'."
            })

        if path == "/api/allocations/revoke":
            alloc_id = str(body.get("id") or body.get("claim_token") or "").strip()
            return self._send_json({"success": self.vault.revoke_allocation(alloc_id)})

        if path == "/api/allocations/delete":
            alloc_id = str(body.get("id") or body.get("claim_token") or "").strip()
            return self._send_json({"success": self.vault.delete_allocation(alloc_id)})

        if path in ("/api/create_key", "/api/keys/import"):
            return self._handle_create_key(body, client)

        if path == "/api/revoke_key":
            key_id = str(body.get("key_id", "")).strip()
            if not key_id:
                raise HTTPError("Missing key_id")
            removed_from_vault = self.vault.remove_venice_key(key_id)
            remote_res = {}
            if self.vault.get_venice_admin_key() and not key_id.startswith("vk_"):
                remote_res = client.delete_key(key_id=key_id)
            if removed_from_vault or remote_res.get("success"):
                return self._send_json({"success": True, "key_id": key_id, "message": "Key revoked and removed from vault"})
            return self._send_json({"success": False, "error": remote_res.get("error", "Key not found in vault")})

        if path == "/api/deploy_key":
            key_str = body.get("key_string", "")
            target_path = body.get("target_path")
            for k in self.vault.get_venice_keys():
                if k.get("id") == key_str and k.get("apiKey"):
                    key_str = k.get("apiKey")
                    break
            if not key_str:
                raise HTTPError("Missing key_string (key id or raw key)")
            res = ConfigDeployer.deploy_venice_key(key_str, Path(target_path) if target_path else None,
                                                   agent_name=str(body.get("agent_name") or ""))
            return self._send_json(res)

        if path == "/api/infer":
            prompt = str(body.get("prompt", "")).strip()
            if not prompt:
                raise HTTPError("Prompt cannot be empty")
            model = body.get("model") or "deepseek-v4-flash"
            max_tokens = _to_int(body.get("max_tokens"), 120, "max_tokens")
            temperature = _to_float(body.get("temperature"), 0.7, "temperature")
            key_id = body.get("key_id", "")

            if key_id:
                key_entry = next((k for k in self.vault.get_venice_keys()
                                  if k.get("id") == key_id or k.get("apiKey") == key_id), None)
                if key_entry:
                    key_max_tier = key_entry.get("maxModelTier", "xl")
                    req_tier = resolve_model_tier(model)
                    if not is_tier_allowed(req_tier, key_max_tier):
                        return self._send_json({
                            "success": False,
                            "error": f"Model tier '{req_tier.upper()}' ({model}) exceeds key's maximum allowed tier '{key_max_tier.upper()}'. Access denied.",
                            "requested_tier": req_tier,
                            "max_tier": key_max_tier
                        }, status=403)

            res = client.test_inference(prompt=prompt, model=model, max_tokens=max_tokens, temperature=temperature)
            return self._send_json(res)

        if path == "/api/agent_bots/register":
            agent_name = str(body.get("agent_name", "")).strip()
            bot_token = str(body.get("bot_token", "")).strip()
            if not agent_name or not bot_token:
                raise HTTPError("Both 'agent_name' and 'bot_token' are required")
            res = self.tg_manager.register_agent_bot(agent_name, bot_token,
                                                     config_path=body.get("config_path", ""),
                                                     notes=body.get("notes", ""))
            return self._send_json(redact(res))

        if path == "/api/agent_bots/remove":
            agent_name = str(body.get("agent_name", "")).strip()
            removed = self.vault.remove_agent_bot(agent_name)
            return self._send_json({"success": removed, "agent_name": agent_name,
                                    "error": None if removed else "Agent bot not found"},
                                   status=200 if removed else 404)

        if path == "/api/agent_bots/deploy":
            res = self.tg_manager.deploy_agent_bot(body.get("agent_name", ""), target_path=body.get("target_path"))
            return self._send_json(redact(res))

        if path == "/api/agent_bots/test":
            agent_name = body.get("agent_name", "")
            bot = self.vault.get_agent_bot(agent_name)
            if not bot:
                return self._send_json({"success": False, "error": f"Agent '{agent_name}' not found"}, status=404)
            return self._send_json(redact(self.tg_manager.send_test_message(bot["bot_token"])))

        if path == "/api/agent_bots/botfather_wizard":
            res = self.tg_manager.generate_botfather_wizard(body.get("agent_name", "hermes-agent"),
                                                            suggested_name=body.get("suggested_name", ""))
            return self._send_json({"success": True, "wizard": res})

        if path == "/api/config":
            if body.get("admin_key"):
                self.vault.set_venice_admin_key(str(body["admin_key"]))
            if body.get("inference_key"):
                self.vault.set_venice_inference_key(str(body["inference_key"]))
            if "authorized_chat_id" in body and str(body["authorized_chat_id"]).strip():
                self.vault.set_authorized_chat_id(str(body["authorized_chat_id"]).strip())
            if body.get("cloudflare_domain"):
                cf = self.vault.get_cloudflare_config()
                self.vault.set_cloudflare_config(str(body["cloudflare_domain"]).strip(),
                                                 tunnel_name=cf.get("tunnel_name", ""),
                                                 tunnel_token=cf.get("tunnel_token", ""))
            return self._send_json({"success": True, "message": "Configuration updated"})

        if path == "/api/balance/thresholds":
            low = _to_float(body.get("low_usd"), 1.0, "low_usd")
            out = _to_float(body.get("out_usd"), 0.05, "out_usd")
            if low < 0 or out < 0:
                raise HTTPError("Thresholds must be positive")
            if out >= low:
                raise HTTPError("'out_usd' must be lower than 'low_usd'")
            return self._send_json({"success": True, "thresholds": self.vault.set_balance_thresholds(low_usd=low, out_usd=out)})

        if path == "/api/pairing/rotate":
            new_code = self.vault.generate_new_pairing_code()
            env_override = bool(os.environ.get("SECURE_PAIRING_CODE"))
            return self._send_json({
                "success": True,
                "pairing_code": self.vault.get_pairing_code(),
                "rotated_to": new_code,
                "env_override": env_override,
                "message": ("Pairing code rotated. Other paired browsers and agents must re-pair."
                            if not env_override else
                            "Stored code rotated, but SECURE_PAIRING_CODE in the environment still takes precedence.")
            })

        if path == "/api/mcp/call":
            name = str(body.get("name") or body.get("tool") or "").strip()
            if not name:
                raise HTTPError("Missing tool 'name'")
            args = body.get("arguments") or body.get("args") or {}
            if not isinstance(args, dict):
                raise HTTPError("'arguments' must be an object")
            known = {t["name"] for t in self._mcp().get_tool_definitions()}
            if name not in known:
                raise HTTPError(f"Unknown MCP tool '{name}'", status=404)
            result = self._mcp().execute_tool(name, args)
            return self._send_json({"success": True, "tool": name, "result": result})

        if path == "/api/mcp/rpc":
            # JSON-RPC 2.0 MCP transport over HTTP (same methods as stdio server).
            res = self._mcp().handle_request(body)
            return self._send_json(res if res is not None else {"jsonrpc": "2.0", "result": None})

        if path == "/api/vault/backup":
            return self._send_json(self.vault.create_backup(label=str(body.get("label", ""))))

        if path == "/api/vault/recall":
            return self._send_json(self.vault.auto_recall(sync_venice_remote=bool(body.get("sync_remote", True))))

        if path == "/api/vault/restore":
            target = body.get("path") or body.get("filename")
            if not target:
                raise HTTPError("Missing backup file path to restore")
            resolved = self.vault.resolve_backup_path(str(target))
            if not resolved:
                raise HTTPError("Unknown backup. Pick one from /api/vault/backups.", status=404)
            return self._send_json(self.vault.restore_from_file(resolved))

        if path == "/api/service/restart":
            return self._send_json(self._supervisor().request_restart())

        if path == "/api/service/install":
            return self._send_json(self._supervisor().install())

        return self._send_json({"success": False, "error": "Endpoint not found"}, status=404)

    def _handle_create_key(self, body: Dict[str, Any], client: VeniceClient) -> None:
        mode = body.get("mode")
        key_str = str(body.get("key_string", "") or "").strip()
        desc = body.get("description") or "Agent Key"
        k_type = str(body.get("key_type", "INFERENCE") or "INFERENCE").upper()
        limit = body.get("limit_usd")
        period = body.get("limit_period", "MONTH")
        expires = body.get("expires_at") or None
        admin_key_param = str(body.get("admin_key", "") or "").strip()
        max_tier = str(body.get("max_model_tier") or "xl").lower().strip()
        if max_tier not in MODEL_TIER_ORDER:
            max_tier = "xl"
        limit_val = _to_float(limit, 0.0, "limit_usd") if limit not in (None, "") else None

        # Mode 1: Import existing key into vault
        if mode == "import" or (key_str and mode != "generate"):
            if not key_str:
                return self._send_json({"success": False, "error": "API Key string cannot be empty"}, status=400)

            rate_res = client.get_rate_limits(key=key_str)
            if not rate_res.get("success"):
                err_msg = rate_res.get("error", "Key verification failed")
                if rate_res.get("status_code") == 401:
                    err_msg = "Venice rejected this API key (401 Unauthorized). Verify the key is valid."
                return self._send_json({"success": False, "error": err_msg, "status_code": rate_res.get("status_code", 400)})

            key_id = f"vk_{hashlib.sha256(key_str.encode()).hexdigest()[:10]}"
            key_obj = {
                "id": key_id,
                "apiKey": key_str,
                "description": desc or "Imported Venice Key",
                "apiKeyType": k_type,
                "maxModelTier": max_tier,
                "consumptionLimits": {"usd": limit_val} if limit_val else {},
                "usage": {"trailingSevenDays": {"usd": 0.0}},
                "createdAt": datetime.utcnow().isoformat() + "Z",
                "status": "ACTIVE",
                "balances": rate_res.get("balances", {}),
                "apiTier": rate_res.get("apiTier", {})
            }
            self.vault.store_venice_key(key_obj)
            if k_type == "ADMIN":
                self.vault.set_venice_admin_key(key_str)
            if not self.vault.get_venice_inference_key():
                self.vault.set_venice_inference_key(key_str)
            return self._send_json({
                "success": True,
                "imported": True,
                "key": redact(key_obj),
                "message": "Key imported and verified successfully"
            })

        # Mode 2: Generate remotely via Venice Admin API
        if admin_key_param:
            self.vault.set_venice_admin_key(admin_key_param)
        active_admin = self.vault.get_venice_admin_key()
        if not active_admin:
            return self._send_json({
                "success": False,
                "error": "Venice Admin API Key is required to issue keys remotely. Enter your Admin Key or import an existing key.",
                "requires_admin": True
            })

        res = client.create_key(
            description=desc,
            key_type=k_type,
            limit_usd=limit_val,
            limit_period=period,
            expires_at=expires,
            admin_key=active_admin
        )
        if res.get("success"):
            key_obj = res.get("key", {})
            key_obj["maxModelTier"] = max_tier
            self.vault.store_venice_key(key_obj)
            # The freshly generated secret is returned exactly once so it can be copied.
        return self._send_json(res)


class VeniceHTTPServer(ThreadingHTTPServer):
    """Threaded server that refuses to share its port on Windows.

    Python's ``HTTPServer`` sets ``SO_REUSEADDR``; on Windows that allows a
    second process to bind the *same* port and silently split traffic with an
    orphaned old instance (e.g. after a crash/restart). Linux/macOS keep the
    default because there it only skips TIME_WAIT.
    """

    daemon_threads = True
    allow_reuse_address = os.name != "nt"


def start_web_server(host: str = "0.0.0.0", port: int = 8844, daemon: bool = False,
                     vault: Optional[KeyVault] = None,
                     tunnel_manager: Optional[CloudflareTunnelManager] = None) -> ThreadingHTTPServer:
    """Start the dashboard server.

    Pass ``vault`` (and ``tunnel_manager``) to share the same instances with the
    Telegram bot / tunnel started by ``run.py`` so every component sees one state.
    """
    if vault is not None:
        DashboardRequestHandler.vault = vault
    if tunnel_manager is not None:
        DashboardRequestHandler.tunnel_manager = tunnel_manager
    httpd = VeniceHTTPServer((host, port), DashboardRequestHandler)
    try:
        print(f"Venice & Telegram Key Management Dashboard running at http://localhost:{httpd.server_address[1]}")
    except Exception:
        pass
    if daemon:
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        return httpd
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down web server...")
        httpd.shutdown()
    return httpd


if __name__ == "__main__":
    start_web_server(port=8844, daemon=False)
