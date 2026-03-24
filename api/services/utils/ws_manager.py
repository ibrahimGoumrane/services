from threading import Lock

from fastapi import WebSocket


class WebSocketManager:
    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = {}
        self._lock = Lock()

    async def connect(self, job_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        with self._lock:
            self._connections.setdefault(job_id, set()).add(websocket)

    async def disconnect(self, job_id: str, websocket: WebSocket) -> None:
        with self._lock:
            subscribers = self._connections.get(job_id)
            if subscribers is None:
                return
            subscribers.discard(websocket)
            if not subscribers:
                self._connections.pop(job_id, None)

    async def send_event(self, job_id: str, event_type: str, data: dict) -> None:
        with self._lock:
            subscribers = list(self._connections.get(job_id, set()))

        stale_connections: list[WebSocket] = []
        payload = {"type": event_type, "data": data}
        for websocket in subscribers:
            try:
                await websocket.send_json(payload)
            except Exception:
                stale_connections.append(websocket)

        for websocket in stale_connections:
            await self.disconnect(job_id, websocket)


ws_manager = WebSocketManager()
