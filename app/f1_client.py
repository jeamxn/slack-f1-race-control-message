"""F1 라이브타이밍 SignalR 클라이언트.

F1 공식 라이브타이밍은 SignalR Core(ASP.NET Core SignalR) 프로토콜을 쓴다.
엔드포인트: wss://livetiming.formula1.com/signalrcore

연결 절차 (FastF1 fastf1/livetiming/client.py 참고):
  1. OPTIONS https://livetiming.formula1.com/signalrcore/negotiate
       -> 응답 쿠키에서 AWSALBCORS 토큰을 얻어 헤더에 넣는다 (로드밸런서 고정용)
  2. HubConnectionBuilder 로 wss://.../signalrcore 에 연결
  3. send("Subscribe", [[topics]])
       - 구독 직후 '현재 상태 스냅샷'은 이 호출의 invocation completion 결과로 온다
       - 이후 변경분은 'feed' 이벤트로 push 된다

라이브타이밍 피드는 인증 없이 구독 가능하므로 access_token_factory 는 쓰지 않는다.
"""
import logging
from typing import Callable

import requests
from signalrcore.hub_connection_builder import HubConnectionBuilder
from signalrcore.messages.completion_message import CompletionMessage

logger = logging.getLogger(__name__)

NEGOTIATE_URL = "https://livetiming.formula1.com/signalrcore/negotiate"
CONNECTION_URL = "wss://livetiming.formula1.com/signalrcore"

# 구독할 토픽. RaceControlMessages + 순위/피트/타이어 추적용,
# 나머지는 컨텍스트(어느 세션인지 등).
TOPICS = [
    "RaceControlMessages",
    "TimingData",
    "TimingAppData",
    "DriverList",
    "SessionInfo",
    "TrackStatus",
]


class F1LiveClient:
    """F1 라이브타이밍 SignalR Core 스트림에 붙어 메시지를 콜백으로 흘려보낸다.

    on_message(topic: str, content: object) 형태로 콜백을 호출한다.
    - 구독 직후 스냅샷: completion 결과의 각 토픽을 한 번씩
    - 이후 변경분: feed 이벤트의 각 (topic, content) 쌍을
    """

    def __init__(
        self,
        on_message: Callable[[str, object], None],
        topics: list[str] | None = None,
    ) -> None:
        self._on_message = on_message
        self._topics = topics or TOPICS
        self._connection = None

    @staticmethod
    def _negotiate_headers() -> dict[str, str]:
        """OPTIONS 프리플라이트로 AWSALBCORS 쿠키를 받아 헤더로 만든다."""
        resp = requests.options(NEGOTIATE_URL, timeout=30)
        cookie = resp.cookies.get("AWSALBCORS")
        if not cookie:
            logger.warning("AWSALBCORS 쿠키를 받지 못함 — 연결이 불안정할 수 있음.")
            return {}
        return {"Cookie": f"AWSALBCORS={cookie}"}

    def _handle_feed(self, msg: object) -> None:
        """'feed' 이벤트(변경분) 처리. signalrcore는 list 형태로 넘긴다.

        형태: [topic, content, timestamp]
        """
        if isinstance(msg, list) and len(msg) >= 2:
            topic, content = msg[0], msg[1]
            self._dispatch(topic, content)
        else:
            logger.debug("예상치 못한 feed 형태: %s", str(msg)[:200])

    def _handle_snapshot(self, msg: CompletionMessage) -> None:
        """Subscribe 호출의 completion 결과(현재 상태 스냅샷) 처리."""
        result = getattr(msg, "result", None)
        if not isinstance(result, dict):
            return
        for topic, content in result.items():
            self._dispatch(topic, content)

    def _dispatch(self, topic: object, content: object) -> None:
        if isinstance(topic, str):
            try:
                self._on_message(topic, content)
            except Exception:  # noqa: BLE001
                logger.exception("on_message 콜백에서 예외 발생")

    def _build(self) -> None:
        headers = self._negotiate_headers()
        self._connection = (
            HubConnectionBuilder()
            .with_url(
                CONNECTION_URL,
                options={"verify_ssl": True, "headers": headers},
            )
            .with_automatic_reconnect(
                {
                    "type": "interval",
                    "intervals": [1, 3, 5, 10, 15, 30],
                }
            )
            .build()
        )
        self._connection.on_open(self._on_open)
        self._connection.on_close(lambda: logger.info("연결 종료됨."))
        self._connection.on_error(lambda e: logger.warning("연결 오류: %s", e))
        self._connection.on("feed", self._handle_feed)

    def _on_open(self) -> None:
        logger.info("연결 수립됨. 구독 요청: %s", ", ".join(self._topics))
        self._connection.send(
            "Subscribe",
            [self._topics],
            on_invocation=self._handle_snapshot,
        )

    def start(self) -> None:
        """연결 시작 (논블로킹). 내부적으로 백그라운드 스레드에서 동작."""
        self._build()
        self._connection.start()

    def stop(self) -> None:
        if self._connection is not None:
            try:
                self._connection.stop()
            except Exception:  # noqa: BLE001
                pass
