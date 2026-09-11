"""F1 라이브타이밍 SignalR 클라이언트.

F1 공식 라이브타이밍은 SignalR Core(ASP.NET Core SignalR) 프로토콜을 쓴다.
엔드포인트: wss://livetiming.formula1.com/signalrcore

연결 절차:
  1. OPTIONS https://livetiming.formula1.com/signalrcore/negotiate
       -> 응답 쿠키에서 AWSALBCORS 토큰을 얻어 헤더에 넣는다 (로드밸런서 고정용)
  2. HubConnectionBuilder 로 wss://.../signalrcore 에 연결
  3. send("Subscribe", [[topics]])
       - 구독 직후 '현재 상태 스냅샷'은 이 호출의 invocation completion 결과로 온다
       - 이후 변경분은 'feed' 이벤트로 push 된다

이 모듈은 '연결 한 번'만 책임진다. 재연결/세션 전환은 supervisor 담당.
signalrcore 의 자동 재연결(with_automatic_reconnect)은 일부러 안 쓴다:
  - interval 핸들러는 인터벌 목록을 다 쓰면 ValueError 를 던지고 영원히 멈췄다
  - 재연결 성공 시 on_open 이 아니라 on_reconnect 만 불려서,
    구독(Subscribe)이 다시 안 나간다 = 연결은 살아있는데 데이터는 안 오는 좌비 상태
(그래도 혹시 몰라 on_reconnect 에도 구독을 묶어둔다.)
라이브타이밍 피드는 인증 없이 구독 가능하므로 access_token_factory 는 쓰지 않는다.
"""
import logging
import threading
import time
from typing import Callable, Optional

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

# 핸드쉐이크 + 구독까지 기다릴 시간(초).
HANDSHAKE_TIMEOUT = 20.0


class F1LiveClient:
    """F1 라이브타이밍 SignalR Core 스트림에 붙어 메시지를 콜백으로 흘려보낸다.

    on_message(topic: str, content: object) 형태로 콜백을 호출한다.
    - 구독 직후 스냅샷: completion 결과의 각 토픽을 한 번씩
    - 이후 변경분: feed 이벤트의 각 (topic, content) 쌍을
    """

    def __init__(
        self,
        on_message: Callable[[str, object], None],
        topics: Optional[list] = None,
    ) -> None:
        self._on_message = on_message
        self._topics = list(topics or TOPICS)
        self._connection = None
        self._open = threading.Event()
        self._lock = threading.Lock()
        self._last_message_at = 0.0
        self._message_count = 0

    # ---- 상태 ----

    @property
    def connected(self) -> bool:
        return self._open.is_set()

    @property
    def last_message_at(self) -> float:
        """마지막으로 데이터를 받은 시각(time.time()). 없으면 0."""
        return self._last_message_at

    @property
    def message_count(self) -> int:
        return self._message_count

    # ---- 연결 ----

    @staticmethod
    def _negotiate_headers() -> dict:
        """OPTIONS 프리플라이트로 AWSALBCORS 쿠키를 받아 헤더로 만든다.

        쿠키는 연결할 때마다 새로 받는다 (오래된 쿠키는 죽은 타겟으로 붙을 수 있음).
        """
        try:
            resp = requests.options(NEGOTIATE_URL, timeout=15)
        except requests.RequestException as exc:
            logger.warning("negotiate 프리플라이트 실패: %s", exc)
            return {}
        cookie = resp.cookies.get("AWSALBCORS")
        if not cookie:
            logger.warning("AWSALBCORS 쿠키를 받지 못함 — 연결이 불안정할 수 있음.")
            return {}
        return {"Cookie": f"AWSALBCORS={cookie}"}

    def connect(self, timeout: float = HANDSHAKE_TIMEOUT) -> bool:
        """새 연결을 맺고 구독까지 끝낸다. 예외를 던지지 않고 성공 여부를 돌려준다."""
        self.close()
        with self._lock:
            self._open.clear()
            try:
                connection = (
                    HubConnectionBuilder()
                    .with_url(
                        CONNECTION_URL,
                        options={
                            "verify_ssl": True,
                            "headers": self._negotiate_headers(),
                        },
                    )
                    .build()
                )
                connection.on_open(lambda: self._on_open(connection))
                connection.on_reconnect(lambda: self._on_open(connection))
                connection.on_close(self._on_close)
                connection.on_error(lambda e: logger.warning("연결 오류: %s", e))
                connection.on("feed", self._handle_feed)
                self._connection = connection
                connection.start()
            except Exception:  # noqa: BLE001
                logger.exception("연결 시도 중 예외")
                self._teardown()
                return False

        if not self._open.wait(timeout):
            logger.warning("연결/구독이 %.0f초 안에 안 끝남 — 끊고 재시도", timeout)
            self.close()
            return False
        return True

    def close(self) -> None:
        """연결 종료. 연결이 없으면 아무것도 안 한다."""
        connection = self._connection
        if connection is None:
            return
        self._open.clear()
        self._connection = None
        try:
            connection.stop()
        except Exception:  # noqa: BLE001
            logger.debug("연결 종료 중 예외(무시)", exc_info=True)

    # 이전 버전 호환용 별칭
    stop = close

    def _teardown(self) -> None:
        connection = self._connection
        self._connection = None
        self._open.clear()
        if connection is not None:
            try:
                connection.stop()
            except Exception:  # noqa: BLE001
                pass

    # ---- 콜백 ----

    def _on_open(self, connection) -> None:
        if connection is not self._connection:
            return  # 이미 버린 연결의 늦은 콜백
        logger.info("연결 수립됨. 구독 요청: %s", ", ".join(self._topics))
        try:
            connection.send(
                "Subscribe",
                [self._topics],
                on_invocation=self._handle_snapshot,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Subscribe 전송 실패")
            return
        self._last_message_at = time.time()
        self._open.set()

    def _on_close(self) -> None:
        if self._open.is_set():
            logger.info("연결 종료됨.")
        self._open.clear()

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
        logger.info("스냅샷 수신: %s", ", ".join(result.keys()))
        for topic, content in result.items():
            self._dispatch(topic, content)

    def _dispatch(self, topic: object, content: object) -> None:
        self._last_message_at = time.time()
        self._message_count += 1
        if isinstance(topic, str):
            try:
                self._on_message(topic, content)
            except Exception:  # noqa: BLE001
                logger.exception("on_message 콜백에서 예외 발생")
