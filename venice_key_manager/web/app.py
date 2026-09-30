import os
import json
import asyncio
import httpx
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from pydantic import BaseModel

from fastapi import (
    FastAPI,
    HTTPException,
    Request,
    Response,
    Query,
    UploadFile,
    File,
    Depends,
    Header,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware

from ..config import config
from ..client import VeniceClient
from ..models import (
    KeyCreateRequest,
    KeyUpdateRequest,
    KeyCycleRequest,
    InferenceTestRequest,
    BatchAuthTokenCreateRequest,
    BatchAuthTokenResponse,
    BatchKeyCreateRequest,
    BatchKeyCreateResponse,
    ProjectCreateRequest,
    ProjectUpdateRequest,
    ExternalKeyCreateRequest,
    ExternalKeyUpdateRequest,
    SubKeyCreateRequest,
    GatewayAllocationResponse,
    MODEL_TIER_MAPPING,
    MODEL_TIER_ORDER,
    is_tier_allowed,
    resolve_model_tier,
    get_model_for_tier,
)
from ..state import state_store

app = FastAPI(
    title="Venice Key Manager",
    description="Real-time Web Dashboard, Key Management & Inference Operations for Venice.ai",
    version="1.0.0",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

client = VeniceClient()


class AuthVerifyRequest(BaseModel):
    token: str


def extract_token_from_request(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_access_token: Optional[str] = Header(None, alias="X-Access-Token"),
    token: Optional[str] = Query(None),
) -> Optional[str]:
    """Extract auth token from query string, custom header, Bearer header, or cookie."""
    # 1. Query parameter
    if isinstance(token, str) and token.strip():
        return token.strip()
    
    q_token = request.query_params.get("token") or request.query_params.get("key") or request.query_params.get("auth")
    if q_token and isinstance(q_token, str) and q_token.strip():
        return q_token.strip()

    # 2. Custom header X-Access-Token
    if isinstance(x_access_token, str) and x_access_token.strip():
        return x_access_token.strip()
    hdr_x = request.headers.get("X-Access-Token")
    if hdr_x and hdr_x.strip():
        return hdr_x.strip()

    # 3. Bearer authorization header
    auth_val = authorization if isinstance(authorization, str) else request.headers.get("Authorization")
    if auth_val:
        parts = auth_val.strip().split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()
        elif len(parts) == 1:
            return parts[0].strip()

    # 4. Cookie
    cookie_token = request.cookies.get("vkm_auth_token")
    if cookie_token and isinstance(cookie_token, str) and cookie_token.strip():
        return cookie_token.strip()

    return None


def require_auth(request: Request, token: Optional[str] = Depends(extract_token_from_request)):
    """Enforce authentication on protected API endpoints."""
    if not token or not state_store.validate_auth_token(token):
        raise HTTPException(
            status_code=401,
            detail="Authentication required. Please submit a valid Telegram access key."
        )
    return token


@app.get("/", response_class=HTMLResponse)
@app.head("/")
async def index_page(
    request: Request,
    token: Optional[str] = Query(None),
    key: Optional[str] = Query(None),
    auth: Optional[str] = Query(None),
    extracted: Optional[str] = Depends(extract_token_from_request),
):
    candidate_key = token or key or auth

    # 1. URL pairing key auto-login: ?token=, ?key=, or ?auth=
    if candidate_key and state_store.validate_auth_token(candidate_key):
        response = templates.TemplateResponse("index.html", {
            "request": request,
            "url_token": candidate_key.strip(),
            "initially_authenticated": True,
        }, status_code=200)
        response.set_cookie(
            key="vkm_auth_token",
            value=candidate_key.strip(),
            max_age=7 * 24 * 3600,
            httponly=True,
            samesite="lax",
        )
        return response

    # 2. Check if user has an existing valid session via cookie or Authorization header
    if extracted and state_store.validate_auth_token(extracted):
        return templates.TemplateResponse("index.html", {
            "request": request,
            "url_token": extracted,
            "initially_authenticated": True,
        }, status_code=200)

    # 3. UNPAIRED: DO NOT LOAD THE SERVICE! Return status 401 with pairing gate.
    return templates.TemplateResponse("pairing_gate.html", {
        "request": request,
    }, status_code=401)


# =============================================================================
# Auth Endpoints (Telegram-generated session keys)
# =============================================================================

@app.post("/api/auth/verify")
async def verify_auth_token(req: AuthVerifyRequest, response: Response):
    token = req.token.strip()
    if not state_store.validate_auth_token(token):
        raise HTTPException(status_code=401, detail="Invalid or expired access key.")
    response.set_cookie(
        key="vkm_auth_token",
        value=token,
        max_age=7 * 24 * 3600,
        httponly=True,
        samesite="lax",
    )
    return {"valid": True, "token": token}


@app.get("/api/auth/status")
async def check_auth_status(token: Optional[str] = Depends(extract_token_from_request)):
    if token and state_store.validate_auth_token(token):
        return {"authenticated": True}
    return {"authenticated": False}


@app.post("/api/auth/logout")
async def logout(response: Response):
    response.delete_cookie("vkm_auth_token")
    return {"success": True}


@app.post("/api/auth/tokens/batch", dependencies=[Depends(require_auth)])
async def create_batch_tokens_endpoint(req: BatchAuthTokenCreateRequest, request: Request):
    """Create a batch of access/pairing codes with a custom prefix."""
    try:
        tokens = state_store.create_batch_auth_tokens(
            prefix=req.prefix,
            count=req.count,
            created_by="web_admin",
            ttl_hours=req.ttl_hours,
            notes=req.notes,
        )
        base_url = str(request.base_url).rstrip("/")
        for t in tokens:
            t["magic_url"] = f"{base_url}/?token={t['token']}"
        return {
            "success": True,
            "count": len(tokens),
            "prefix": req.prefix,
            "tokens": tokens,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/auth/tokens", dependencies=[Depends(require_auth)])
async def list_auth_tokens_endpoint(prefix: Optional[str] = Query(None), request: Request = None):
    """List active access/pairing tokens, optionally filtered by prefix."""
    try:
        tokens = state_store.list_active_tokens(prefix=prefix)
        base_url = str(request.base_url).rstrip("/") if request else ""
        for t in tokens:
            t["magic_url"] = f"{base_url}/?token={t['token']}" if base_url else f"/?token={t['token']}"
        return tokens
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/auth/tokens/{token_str}", dependencies=[Depends(require_auth)])
async def revoke_auth_token_endpoint(token_str: str):
    """Revoke an active access/pairing code."""
    try:
        ok = state_store.revoke_auth_token(token_str)
        return {"success": ok, "token": token_str}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/health")
async def health_check():
    return {"status": "ok", "app": "venice-key-manager", "version": "1.0.0"}


# =============================================================================
# Account & Rate Limits
# =============================================================================

@app.get("/api/balance", dependencies=[Depends(require_auth)])
async def get_balance():
    try:
        rates = await client.get_rate_limits()
        thresh = state_store.get_global_threshold()
        is_low = rates.balances.USD <= thresh
        return {
            "balances": rates.balances.model_dump(),
            "access_permitted": rates.accessPermitted,
            "next_epoch_begins": rates.nextEpochBegins,
            "global_threshold": thresh,
            "is_low_balance": is_low,
            "api_tier": rates.apiTier.model_dump() if rates.apiTier else None,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# Keys Management (CRUD + Cycle)
# =============================================================================

@app.get("/api/keys", dependencies=[Depends(require_auth)])
async def list_keys(category: Optional[str] = Query(None)):
    try:
        keys = await client.list_keys(category_filter=category)
        return [k.model_dump() for k in keys]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/keys", dependencies=[Depends(require_auth)])
async def create_key(req: KeyCreateRequest):
    try:
        res = await client.create_key(req)
        return res.model_dump()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/keys/batch", dependencies=[Depends(require_auth)])
async def create_batch_keys_endpoint(req: BatchKeyCreateRequest):
    """Create a batch of Venice API keys with a common name/description prefix."""
    try:
        results = await client.create_batch_keys(
            prefix=req.prefix,
            count=req.count,
            daily_usd=req.daily_usd,
            category=req.category,
            api_key_type=req.apiKeyType,
            limit_period=req.limitPeriod,
            custom_threshold=req.custom_threshold,
        )
        return {
            "success": True,
            "count": len(results),
            "prefix": req.prefix,
            "keys": [r.model_dump() for r in results],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/api/keys/{key_id}", dependencies=[Depends(require_auth)])
async def update_key(key_id: str, req: KeyUpdateRequest):
    req.id = key_id
    try:
        success = await client.update_key(req)
        return {"success": success, "id": key_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/keys/{key_id}", dependencies=[Depends(require_auth)])
async def revoke_key(key_id: str):
    try:
        success = await client.revoke_key(key_id)
        return {"success": success, "id": key_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/keys/{key_id}/cycle", dependencies=[Depends(require_auth)])
async def cycle_key(key_id: str, req: KeyCycleRequest):
    req.id = key_id
    try:
        new_key_resp, revoked_old = await client.cycle_key(req)
        return {
            "status": "cycled",
            "new_key": new_key_resp.model_dump(),
            "old_key_id": key_id,
            "old_key_revoked": revoked_old,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# Models & Inference
# =============================================================================

@app.get("/api/models", dependencies=[Depends(require_auth)])
async def list_models(privacy: Optional[str] = Query(None)):
    try:
        models = await client.list_models()
        if privacy and privacy.lower() != "all":
            models = [m for m in models if m.privacy.lower() == privacy.lower()]
        return [m.model_dump() for m in models]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/inference/test", dependencies=[Depends(require_auth)])
async def test_inference(req: InferenceTestRequest):
    try:
        res = await client.test_inference(
            prompt=req.prompt,
            model=req.model,
            api_key=req.api_key,
        )
        return res.model_dump()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# Categories & Settings
# =============================================================================

@app.get("/api/categories", dependencies=[Depends(require_auth)])
async def get_categories():
    return {"categories": state_store.get_categories()}


@app.post("/api/categories", dependencies=[Depends(require_auth)])
async def add_category(data: Dict[str, str]):
    cat = data.get("category", "")
    added = state_store.add_category(cat)
    return {"success": added, "categories": state_store.get_categories()}


@app.delete("/api/categories/{category}", dependencies=[Depends(require_auth)])
async def remove_category(category: str):
    removed = state_store.remove_category(category)
    return {"success": removed, "categories": state_store.get_categories()}


@app.get("/api/settings", dependencies=[Depends(require_auth)])
async def get_settings():
    return {
        "global_threshold": state_store.get_global_threshold(),
        "categories": state_store.get_categories(),
    }


@app.post("/api/settings", dependencies=[Depends(require_auth)])
async def update_settings(data: Dict[str, Any]):
    if "global_threshold" in data:
        state_store.set_global_threshold(float(data["global_threshold"]))
    return {
        "success": True,
        "global_threshold": state_store.get_global_threshold(),
    }


# =============================================================================
# Downloadable State & Settings Backup (Export / Import)
# =============================================================================

@app.get("/api/backup/export", dependencies=[Depends(require_auth)])
async def export_backup():
    try:
        rates = await client.get_rate_limits()
        keys = await client.list_keys()
        summary = {
            "total_keys": len(keys),
            "account_balance_usd": rates.balances.USD,
            "access_permitted": rates.accessPermitted,
        }
    except Exception:
        summary = {}

    backup = state_store.export_backup(additional_summary=summary)
    filename = f"venice-key-manager-backup-{backup.exported_at[:10]}.json"
    content = backup.model_dump_json(indent=2)
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.post("/api/backup/import", dependencies=[Depends(require_auth)])
async def import_backup(file: UploadFile = File(...)):
    try:
        contents = await file.read()
        data = json.loads(contents.decode("utf-8"))
        success = state_store.import_backup(data)
        if not success:
            raise HTTPException(status_code=400, detail="Invalid backup file format.")
        return {"success": True, "message": "Backup imported successfully."}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Import failed: {str(e)}")


# =============================================================================
# Daily Key Usage Report API
# =============================================================================

@app.get("/api/report", dependencies=[Depends(require_auth)])
async def get_daily_report():
    try:
        from ..report import DailyKeyReport
        reporter = DailyKeyReport(client=client)
        data = await reporter.generate_report_data()
        md = reporter.format_markdown(data)
        return {"data": data, "markdown": md}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/report/send", dependencies=[Depends(require_auth)])
async def send_daily_report(chat_id: Optional[str] = Query(None)):
    try:
        from ..report import DailyKeyReport
        reporter = DailyKeyReport(client=client)
        sent = await reporter.send_to_telegram(chat_id=chat_id)
        if not sent:
            raise HTTPException(status_code=500, detail="Failed to dispatch report to Telegram.")
        return {"success": True, "message": "Daily report sent to Telegram."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# Real-Time SSE Feed
# =============================================================================

@app.get("/api/sse/stats", dependencies=[Depends(require_auth)])
async def sse_stats(request: Request):
    """Server-Sent Events streaming live balances, key count, and alerts every 3 seconds."""
    async def event_generator():
        while True:
            if await request.is_disconnected():
                break
            try:
                rates = await client.get_rate_limits()
                keys = await client.list_keys()
                thresh = state_store.get_global_threshold()

                low_keys_count = sum(1 for k in keys if k.is_low_balance)
                total_current_spend = sum(
                    float(k.currentPeriodUsage.usd or 0) for k in keys if k.currentPeriodUsage
                )

                payload = {
                    "balance_usd": rates.balances.USD,
                    "balance_diem": rates.balances.DIEM,
                    "access_permitted": rates.accessPermitted,
                    "is_low_balance": rates.balances.USD <= thresh,
                    "global_threshold": thresh,
                    "total_keys": len(keys),
                    "low_keys_count": low_keys_count,
                    "total_current_spend": round(total_current_spend, 4),
                    "next_epoch_begins": rates.nextEpochBegins,
                }
                yield f"data: {json.dumps(payload)}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'error': str(e)})}\n\n"

            await asyncio.sleep(3.0)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# =============================================================================
# External Key Authentication & Gateway Security
# =============================================================================

def extract_external_key_from_request(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    x_pairing_code: Optional[str] = Header(None, alias="X-Pairing-Code"),
    key: Optional[str] = Query(None),
) -> Optional[str]:
    """Extract external API key or pairing code from headers, query string, or bearer token."""
    # 1. Query parameter
    if key and isinstance(key, str) and key.strip():
        return key.strip()
    for q_param in ("key", "api_key", "token", "pairing_code"):
        val = request.query_params.get(q_param)
        if val and isinstance(val, str) and val.strip():
            return val.strip()

    # 2. Custom header X-API-Key or X-Pairing-Code
    if x_api_key and isinstance(x_api_key, str) and x_api_key.strip():
        return x_api_key.strip()
    if x_pairing_code and isinstance(x_pairing_code, str) and x_pairing_code.strip():
        return x_pairing_code.strip()

    for h_name in ("X-API-Key", "x-api-key", "X-Pairing-Code", "x-pairing-code"):
        h_val = request.headers.get(h_name)
        if h_val and isinstance(h_val, str) and h_val.strip():
            return h_val.strip()

    # 3. Authorization Bearer header
    auth_val = authorization if isinstance(authorization, str) else request.headers.get("Authorization")
    if auth_val:
        parts = auth_val.strip().split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()
        elif len(parts) == 1:
            return parts[0].strip()

    return None


async def verify_gateway_access(
    request: Request,
    token: Optional[str] = Depends(extract_external_key_from_request),
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Validate external key or pairing code and check project/key spending limits."""
    if not token:
        raise HTTPException(
            status_code=401,
            detail={
                "error": {
                    "message": "Missing API Key or Pairing Code. Provide via 'Authorization: Bearer <key>', 'X-API-Key: <key>', or '?key=<key>'.",
                    "type": "authentication_error",
                    "code": "missing_api_key",
                }
            },
        )

    is_valid, key_meta, project, err_msg = state_store.validate_external_key(token)
    if not is_valid:
        raise HTTPException(
            status_code=403,
            detail={
                "error": {
                    "message": err_msg,
                    "type": "permission_denied",
                    "code": "allocation_denied",
                    "key_id": key_meta.get("id") if key_meta else None,
                    "project": project.get("name") if project else None,
                }
            },
        )
    return key_meta, project


# =============================================================================
# Project & Allocation Management APIs (Admin Dashboard)
# =============================================================================

@app.get("/api/projects", dependencies=[Depends(require_auth)])
async def list_projects():
    """List all projects with live spend, limits, and connected keys count."""
    return {"projects": state_store.list_projects()}


@app.post("/api/projects", dependencies=[Depends(require_auth)])
async def create_project(req: ProjectCreateRequest):
    """Create a new project allocation."""
    try:
        proj = state_store.create_project(
            name=req.name,
            description=req.description or "",
            daily_limit_usd=req.daily_limit_usd,
            weekly_limit_usd=req.weekly_limit_usd,
            default_sub_key_daily_usd=req.default_sub_key_daily_usd,
            max_model_tier=req.max_model_tier,
        )
        return {"success": True, "project": proj}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/projects/{project_id}", dependencies=[Depends(require_auth)])
async def get_project(project_id: str):
    """Get project details."""
    proj = state_store.get_project(project_id)
    if not proj:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found.")
    return {"project": proj}


@app.patch("/api/projects/{project_id}", dependencies=[Depends(require_auth)])
async def update_project(project_id: str, req: ProjectUpdateRequest):
    """Update project budget limits, tier ceiling, or status."""
    updates = req.dict(exclude_unset=True)
    proj = state_store.update_project(project_id, **updates)
    if not proj:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found.")
    return {"success": True, "project": proj}


@app.delete("/api/projects/{project_id}", dependencies=[Depends(require_auth)])
async def delete_project(project_id: str):
    """Delete project and deactivate its keys."""
    success = state_store.delete_project(project_id)
    if not success:
        raise HTTPException(status_code=400, detail="Cannot delete default project or project not found.")
    return {"success": True, "message": f"Project '{project_id}' deleted."}


# =============================================================================
# External Keys & Sub-Keys APIs (Admin Dashboard)
# =============================================================================

@app.get("/api/external-keys", dependencies=[Depends(require_auth)])
async def list_external_keys(project_id: Optional[str] = Query(None)):
    """List all external keys, optionally filtered by project."""
    keys = state_store.list_external_keys(project_id=project_id)
    return {"keys": keys}


@app.post("/api/external-keys", dependencies=[Depends(require_auth)])
async def create_external_key(req: ExternalKeyCreateRequest):
    """Generate an external access key for an agent or client service."""
    try:
        key_record = state_store.create_external_key(
            project_id=req.project_id,
            name=req.name,
            daily_limit_usd=req.daily_limit_usd,
            weekly_limit_usd=req.weekly_limit_usd,
            limit_period=req.limit_period,
            max_model_tier=req.max_model_tier,
            prefix=req.prefix,
            notes=req.notes or "",
            created_by="admin",
            key_type="ADMIN_EXTERNAL",
        )
        return {"success": True, "key": key_record}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/external-keys/{key_id}/subkeys", dependencies=[Depends(require_auth)])
async def create_sub_key_admin(key_id: str, req: SubKeyCreateRequest):
    """Admin endpoint to create a sub-key under an existing external key."""
    try:
        sub_key = state_store.create_sub_key(
            parent_key_or_token=key_id,
            name=req.name,
            amount_usd=req.amount_usd,
            period=req.period,
            max_model_tier=req.max_model_tier,
            notes=req.notes,
        )
        return {"success": True, "key": sub_key}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.patch("/api/external-keys/{key_id}", dependencies=[Depends(require_auth)])
async def update_external_key(key_id: str, req: ExternalKeyUpdateRequest):
    """Update allocation limit, model tier, or status of an external key."""
    updates = req.dict(exclude_unset=True)
    k = state_store.update_external_key(key_id, **updates)
    if not k:
        raise HTTPException(status_code=404, detail=f"External key '{key_id}' not found.")
    return {"success": True, "key": k}


@app.delete("/api/external-keys/{key_id}", dependencies=[Depends(require_auth)])
async def revoke_external_key(key_id: str):
    """Revoke an external key."""
    success = state_store.revoke_external_key(key_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"External key '{key_id}' not found.")
    return {"success": True, "message": f"Key '{key_id}' revoked."}


# =============================================================================
# Cloudflare Gateway Configuration & Tier Info APIs
# =============================================================================

class GatewayConfigUpdate(BaseModel):
    cloudflare_gateway_url: Optional[str] = None


@app.get("/api/gateway/info", dependencies=[Depends(require_auth)])
async def get_gateway_info(request: Request):
    """Return Cloudflare gateway URL, available tiers, and client connection snippets."""
    gw_url = state_store.get_cloudflare_gateway_url() or str(request.base_url).rstrip("/")
    return {
        "cloudflare_gateway_url": state_store.get_cloudflare_gateway_url(),
        "effective_gateway_url": gw_url,
        "tiers": MODEL_TIER_MAPPING,
        "tier_order": MODEL_TIER_ORDER,
        "total_projects": len(state_store.list_projects()),
        "total_external_keys": len(state_store.list_external_keys()),
        "curl_example": f"curl -X POST {gw_url}/v1/chat/completions \\\n  -H 'Authorization: Bearer <EXTERNAL_KEY_OR_PAIRING_CODE>' \\\n  -H 'Content-Type: application/json' \\\n  -d '{{\"model\": \"xs\", \"messages\": [{{\"role\": \"user\", \"content\": \"Hello!\"}}]}}'",
    }


@app.post("/api/gateway/config", dependencies=[Depends(require_auth)])
async def update_gateway_config(req: GatewayConfigUpdate):
    """Update public Cloudflare DNS gateway endpoint URL."""
    state_store.set_cloudflare_gateway_url(req.cloudflare_gateway_url)
    return {
        "success": True,
        "cloudflare_gateway_url": state_store.get_cloudflare_gateway_url(),
    }


# =============================================================================
# OpenAI-Compatible External Gateway Endpoints (Via Cloudflare DNS / External Keys)
# =============================================================================

@app.get("/v1/models")
@app.get("/api/gateway/models")
async def gateway_list_models(
    auth: Tuple[Dict[str, Any], Dict[str, Any]] = Depends(verify_gateway_access),
):
    """List available model sizes (xs to xl) and underlying models permitted for this key."""
    key_meta, project = auth
    max_tier = key_meta.get("max_model_tier", "xl")

    models_list = []
    # Add tier representations
    for tier in MODEL_TIER_ORDER:
        if is_tier_allowed(tier, max_tier):
            t_data = MODEL_TIER_MAPPING[tier]
            models_list.append({
                "id": tier,
                "object": "model",
                "created": 1700000000,
                "owned_by": "venice-gateway",
                "permission": [],
                "root": t_data["default_model"],
                "parent": None,
                "tier": tier,
                "label": t_data["label"],
                "description": t_data["description"],
                "default_model": t_data["default_model"],
            })
            # Also include explicit underlying Venice models for this tier
            for m_id in t_data.get("models", []):
                models_list.append({
                    "id": m_id,
                    "object": "model",
                    "created": 1700000000,
                    "owned_by": "venice-ai",
                    "permission": [],
                    "root": m_id,
                    "parent": None,
                    "tier": tier,
                })

    return {"object": "list", "data": models_list}


@app.get("/v1/allocation")
@app.get("/api/gateway/allocation")
async def gateway_get_allocation(
    auth: Tuple[Dict[str, Any], Dict[str, Any]] = Depends(verify_gateway_access),
):
    """Inspect remaining inference balance, allowed tier, and project info for caller's key."""
    key_meta, project = auth
    daily_limit = float(key_meta.get("daily_limit_usd", 0.25))
    spent_today = float(key_meta.get("current_period_spend", 0.0))
    remaining = max(0.0, round(daily_limit - spent_today, 4))

    return GatewayAllocationResponse(
        key_name=key_meta.get("name", "External Key"),
        project_id=project.get("id", ""),
        project_name=project.get("name", "General Project"),
        key_type=key_meta.get("key_type", "EXTERNAL"),
        daily_limit_usd=daily_limit,
        spent_today_usd=spent_today,
        remaining_today_usd=remaining,
        limit_period=key_meta.get("limit_period", "DAY"),
        max_model_tier=key_meta.get("max_model_tier", "xl"),
        status=key_meta.get("status", "active"),
        cloudflare_gateway_url=state_store.get_cloudflare_gateway_url(),
    )


@app.post("/v1/subkeys")
@app.post("/api/gateway/subkeys")
async def gateway_create_subkey(
    req: SubKeyCreateRequest,
    auth: Tuple[Dict[str, Any], Dict[str, Any]] = Depends(verify_gateway_access),
):
    """
    Allow external services/agents to create sub-keys with spend caps per project per day or week.
    By default unless created by admin is: 25 cents per project per day.
    """
    key_meta, project = auth
    parent_token = key_meta.get("token") or key_meta.get("id")

    # Enforce parent tier limit
    requested_tier = req.max_model_tier or key_meta.get("max_model_tier", "xl")
    if not is_tier_allowed(requested_tier, key_meta.get("max_model_tier", "xl")):
        requested_tier = key_meta.get("max_model_tier", "xl")

    # Enforce default: 25 cents per project per day unless created by admin
    amount = req.amount_usd if (req.amount_usd is not None and req.amount_usd > 0) else 0.25
    parent_daily = float(key_meta.get("daily_limit_usd", 0.25))
    # Cap at parent's daily limit
    if req.period.upper() == "DAY" and amount > parent_daily:
        amount = parent_daily

    try:
        subkey = state_store.create_sub_key(
            parent_key_or_token=parent_token,
            name=req.name,
            amount_usd=amount,
            period=req.period,
            max_model_tier=requested_tier,
            notes=req.notes or f"Generated via external gateway by {key_meta.get('name')}",
        )
        return {
            "success": True,
            "sub_key": subkey,
            "message": f"Sub-key generated with ${amount:.2f} USD per {req.period.lower()} allocation.",
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/v1/chat/completions")
@app.post("/api/gateway/chat/completions")
async def gateway_chat_completions(
    request: Request,
    auth: Tuple[Dict[str, Any], Dict[str, Any]] = Depends(verify_gateway_access),
):
    """
    OpenAI-compatible gated chat completion proxy:
    - Enforces model tier hierarchy (xs to xl).
    - Checks remaining project/key allocation.
    - Proxies request to Venice API.
    - Atomically meters token usage and updates balance in real-time.
    """
    key_meta, project = auth

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail={"error": {"message": "Invalid JSON body"}})

    raw_model = body.get("model", "s")
    resolved_tier = resolve_model_tier(raw_model)
    max_tier = key_meta.get("max_model_tier", "xl")

    # 1. Tier enforcement
    if not is_tier_allowed(resolved_tier, max_tier):
        allowed = [t for t in MODEL_TIER_ORDER if is_tier_allowed(t, max_tier)]
        raise HTTPException(
            status_code=403,
            detail={
                "error": {
                    "message": (
                        f"Requested model '{raw_model}' corresponds to tier '{resolved_tier.upper()}', "
                        f"which exceeds this key's maximum tier limit of '{max_tier.upper()}'. "
                        f"Allowed tiers: {', '.join(allowed)}."
                    ),
                    "type": "permission_denied",
                    "code": "model_tier_exceeded",
                    "requested_tier": resolved_tier,
                    "max_tier": max_tier,
                    "allowed_tiers": allowed,
                }
            },
        )

    # 2. Map tier alias to concrete Venice model if needed
    if raw_model.lower().strip() in MODEL_TIER_ORDER:
        body["model"] = get_model_for_tier(resolved_tier)

    # 3. Proxy to Venice API
    response_data = await client.chat_completion(body)

    if "error" in response_data:
        # Pass through upstream error
        return JSONResponse(status_code=502, content=response_data)

    # 4. Compute token consumption and metering cost
    usage = response_data.get("usage", {})
    prompt_tokens = int(usage.get("prompt_tokens", 0))
    completion_tokens = int(usage.get("completion_tokens", 0))
    total_tokens = int(usage.get("total_tokens", prompt_tokens + completion_tokens))

    tier_info = MODEL_TIER_MAPPING.get(resolved_tier, MODEL_TIER_MAPPING["m"])
    cost_usd = (
        (prompt_tokens * tier_info["cost_per_m_in"]) +
        (completion_tokens * tier_info["cost_per_m_out"])
    ) / 1_000_000.0

    # Ensure small nominal cost even on tiny zero-token completions to prevent abuse
    cost_usd = max(0.0001, round(cost_usd, 6))

    # 5. Record usage against key and project
    state_store.record_external_usage(
        key_id=key_meta.get("id"),
        project_id=project.get("id"),
        cost_usd=cost_usd,
        tokens=total_tokens,
    )

    # 6. Attach usage metadata headers
    headers = {
        "X-VKM-Project-Id": str(project.get("id")),
        "X-VKM-Key-Id": str(key_meta.get("id")),
        "X-VKM-Model-Tier": resolved_tier,
        "X-VKM-Cost-USD": f"{cost_usd:.6f}",
    }

    return JSONResponse(content=response_data, headers=headers)
