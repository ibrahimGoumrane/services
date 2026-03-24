from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from api.services.utils.job_manager import job_store
from api.services.utils.ws_manager import ws_manager



router = APIRouter(tags=["websocket"])


@router.websocket("/ws/jobs/{job_id}")
async def subscribe_job_events(websocket: WebSocket, job_id: str) -> None:
    job = job_store.get_job(job_id)
    if job is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Unknown job_id")
        return

    await ws_manager.connect(job_id, websocket)
    await websocket.send_json({"type": "snapshot", "data": job.to_dict()})

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await ws_manager.disconnect(job_id, websocket)
