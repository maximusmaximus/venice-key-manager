import os
import json
import asyncio
from pathlib import Path
from typing import Optional, List, Dict, Any
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
    if token:
        return token.strip()
    if x_access_token:
        return x_access_token.strip()
    if authorization:
        parts = authorization.strip().split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()
        elif len(parts) == 1:
            return parts[0].strip()
    cookie_token = request.cookies.get("vkm_auth_token")
    if cookie_token:
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
async def index_page(request: Request, token: Optional[str] = Query(None)):
    is_valid_url_token = False
    if token and state_store.validate_auth_token(token):
        is_valid_url_token = True
    response = templates.TemplateResponse("index.html", {
        "request": request,
        "url_token": token or "",
        "initially_authenticated": is_valid_url_token,
    })
    if is_valid_url_token:
        response.set_cookie(
            key="vkm_auth_token",
            value=token.strip(),
            max_age=7 * 24 * 3600,
            httponly=True,
            samesite="lax",
        )
    return response


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
