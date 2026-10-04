"""Small runtime control API. No raw Codex messages or credentials cross here."""
from dataclasses import asdict
import json
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from .ai.base import AIError

def local_control(request: Request):
    if request.method == "GET": return
    origin = request.headers.get("origin")
    expected = f"{request.url.scheme}://{request.url.netloc}"
    if (origin and origin != expected) or request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "AI control requires same-origin access")


router = APIRouter(prefix="/api/ai", dependencies=[Depends(local_control)])


def runtime(request): return request.app.state.ai_runtime


def safe_call(fn):
    try: return fn()
    except AIError as error: raise HTTPException(503, str(error)) from None


@router.get("/status")
def status(request: Request, refresh: bool = False):
    ai = runtime(request)
    return {**ai.status(refresh=refresh).public(), "capabilities": asdict(ai.capabilities())}


@router.post("/restart")
def restart(request: Request):
    runtime(request).restart()
    return status(request)


@router.post("/login")
def login(request: Request): return safe_call(runtime(request).login)


@router.post("/login/cancel")
def cancel_login(request: Request):
    safe_call(runtime(request).cancel_login)
    return status(request)


@router.post("/logout")
def logout(request: Request):
    safe_call(runtime(request).logout)
    return status(request)


@router.get("/models")
def models(request: Request):
    return {"models": [asdict(model) for model in safe_call(runtime(request).models)],
            "notice": "Catalog only; entitlement is determined by actual turn results."}


@router.post("/smoke")
def smoke(request: Request):
    ai = runtime(request)
    # Session creation fails before opening SSE when unavailable.
    session = safe_call(ai.create_session)
    def events():
        try:
            for event in ai.send_turn(session, "Reply with exactly: runtime-ok"):
                yield "data: " + json.dumps(asdict(event), ensure_ascii=False) + "\n\n"
        except AIError as error:
            yield "data: " + json.dumps({"kind": "error", "code": error.code}) + "\n\n"
    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})


@router.post("/sessions/{session_id}/turns/{turn_id}/cancel")
def cancel(session_id: str, turn_id: str, request: Request):
    safe_call(lambda: runtime(request).cancel(session_id, turn_id))
    return {"ok": True}
