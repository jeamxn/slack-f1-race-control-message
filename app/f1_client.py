"""F1 라이브타이밍 SignalR 클라이언트.

F1 공식 라이브타이밍은 구형 ASP.NET SignalR(1.x) 프로토콜을 쓴다.
연결 절차:
  1. GET /signalr/negotiate  -> connectionToken 획득
  2. WS  /signalr/connect    -> Streaming 허브에 웹소켓 연결
  3. WS send                 -> Subscribe 메서드 호출로 토픽 구독

들어오는 메시지에서 'RaceControlMessages' 토픽만 골라 콜백으로 넘긴다.
FastF1(fastf1/livetiming/client.py)의 구현을 참고했다.
"""
import json
import logging
import time
import urllib.parse
from typing import Callable

import requests
import websocket

logger = logging.getLogger(__name__)

# 구독할 토픽. RaceControlMessages만 필요하지만, SessionInfo도 같이 받아두면
# 어느 세션인지 로그로 확인하기 좋다.
TOPICS = ["RaceControlMessages", "SessionInfo"]

HEADERS = {
    "User-Agent": "BestHTTP",
    "Accept-Encoding": "gzip, identity",
}

CLIENT_PROTOCOL = "1.5"
HUB = "Streaming"


class F1LiveClient:
    """F1 라이브타이밍 SignalR 스트림에 붙어서 메시지를 콜백으로 흘려보낸다."""

    def __init__(
        self,
        base_url: str,
        on_message: Callable[[str, object], None],
        reconnect_delay: int = 10,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._on_message = on_message
        self._reconnect_delay = reconnect_delay
        self._ws: websocket.WebSocket | None = None

    # ---- 1. negotiate ----
    def _negotiate(self) -> tuple[str, str]:
        """connectionToken과 cookie를 받아온다."""
        conn_data = json.dumps([{"name": HUB}])
        params = {
            "clientProtocol": CLIENT_PROTOCOL,
            "connectionData": conn_data,
        }
        url = f"{self._base}/negotiate?" + urllib.parse.urlencode(params)
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        token = resp.json()["ConnectionToken"]
        cookie = resp.headers.get("Set-Cookie", "")
        return token, cookie

    # ---- 2. connect (websocket) ----
    def _open_ws(self, token: str, cookie: str) -> websocket.WebSocket:
        conn_data = json.dumps([{"name": HUB}])
        params = {
            "clientProtocol": CLIENT_PROTOCOL,
            "transport": "webSockets",
            "connectionToken": token,
            "connectionData": conn_data,
        }
        ws_base = self._base.replace("https://", "wss://").replace("http://", "ws://")
        url = f"{ws_base}/connect?" + urllib.parse.urlencode(params)

        headers = dict(HEADERS)
        if cookie:
            headers["Cookie"] = cookie

        ws = websocket.create_connection(
            url,
            header=[f"{k}: {v}" for k, v in headers.items()],
            timeout=60,
        )
        return ws

    # ---- 3. subscribe ----
    @staticmethod
    def _subscribe(ws: websocket.WebSocket) -> None:
        payload = {
            "H": HUB,
            "M": "Subscribe",
            "A": [TOPICS],
            "I": 1,
        }
        ws.send(json.dumps(payload))

    def _connect_once(self) -> None:
        token, cookie = self._negotiate()
        logger.info("negotiate 성공, 웹소켓 연결 중...")
        self._ws = self._open_ws(token, cookie)
        self._subscribe(self._ws)
        logger.info("구독 완료: %s", ", ".join(TOPICS))

        while True:
            raw = self._ws.recv()
            if not raw:
                continue
            self._handle_raw(raw)

    def _handle_raw(self, raw: str) -> None:
        """SignalR 프레임을 파싱해서 토픽별 콜백 호출."""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.debug("JSON 파싱 실패: %s", raw[:200])
            return

        # 구독 직후 오는 초기 스냅샷: R 필드에 현재 상태가 통째로 들어있다.
        if "R" in data and isinstance(data["R"], dict):
            for topic, content in data["R"].items():
                self._on_message(topic, content)

        # 이후 변경분: M 리스트에 [{H, M, A}] 형태로 들어온다.
        for item in data.get("M", []):
            if item.get("M") == "feed":
                args = item.get("A", [])
                if len(args) >= 2:
                    topic, content = args[0], args[1]
                    self._on_message(topic, content)

    def run_forever(self) -> None:
        """끊기면 재연결하며 무한 루프."""
        while True:
            try:
                self._connect_once()
            except KeyboardInterrupt:
                logger.info("종료 요청 수신.")
                break
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "연결 끊김(%s). %d초 후 재연결.", e, self._reconnect_delay
                )
                self._close()
                time.sleep(self._reconnect_delay)

    def _close(self) -> None:
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:  # noqa: BLE001
                pass
            self._ws = None
