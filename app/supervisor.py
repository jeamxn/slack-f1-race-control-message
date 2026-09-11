"""연결·세션 감시자.

봇을 한 번 띄워두면 다음 GP, 다음 세션도 알아서 잡도록 하는 게 이 모듈의 일.

  - 새 세션이 뜼면 상태를 리셋하고 새로 붙는다 (정적 SessionInfo 폴링 + 피드 푸시)
  - 연결이 끊기면 지수 백오프로 '무한' 재시도한다 (횟수 제한 없음)
  - 세션이 진행 중인데 일정 시간 아무 데이터도 안 오면 좌비 연결로 보고 재연결한다
  - 세션이 없는 동안에도 주기적으로 연결을 재활용한다 (조용히 죽은 소켓 방지)
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from .session import SessionInfo

logger = logging.getLogger(__name__)

# 재연결 백오프(초). 마지막 값으로 계속 재시도한다 — 포기하지 않는다.
BACKOFF_SECONDS = (1, 2, 5, 10, 20, 30, 60)


def _noop(*_args, **_kwargs) -> None:
    return None


@dataclass
class SupervisorHooks:
    """supervisor 가 알려주는 사건들."""

    # 세션 자체가 바뀜을 때 (prev 가 None 이면 봇 기동 후 처음 인식)
    on_session_change: Callable[[Optional[SessionInfo], SessionInfo], None] = _noop
    # 같은 세션의 상태가 바뀜을 때 (Inactive -> Started -> Finalised ...)
    on_session_status: Callable[[SessionInfo, SessionInfo], None] = _noop
    # 연결이 잡혔을 때 (first=프로세스 통틀어 처음 연결인지)
    on_connected: Callable[[bool], None] = _noop


@dataclass
class SupervisorConfig:
    poll_interval: float = 60.0         # 정적 SessionInfo 폴링 주기(평상시)
    live_poll_interval: float = 30.0    # 세션 진행 중 폴링 주기
    silence_timeout: float = 180.0      # 진행 중 이만큼 조용하면 좌비로 판정
    max_connection_age: float = 3600.0  # 비세션 구간에서 연결 재활용 주기
    backoff: tuple = field(default=BACKOFF_SECONDS)


class Supervisor:
    """클라이언트 연결과 세션 전환을 돌보는 상태 기계. tick() 을 주기적으로 불러준다."""

    def __init__(
        self,
        client,
        session_source,
        hooks: Optional[SupervisorHooks] = None,
        config: Optional[SupervisorConfig] = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._client = client
        self._source = session_source
        self._hooks = hooks or SupervisorHooks()
        self._config = config or SupervisorConfig()
        self._clock = clock

        self._session: Optional[SessionInfo] = None
        self._attempts = 0
        self._next_attempt_at = 0.0
        self._connected_at = 0.0
        self._last_poll_at = 0.0
        self._ever_connected = False

    # ---- 상태 ----

    @property
    def session(self) -> Optional[SessionInfo]:
        return self._session

    @property
    def connected(self) -> bool:
        return bool(self._client.connected)

    # ---- 세션 ----

    def observe_session(self, session: Optional[SessionInfo], source: str) -> bool:
        """세션 정보를 반영한다. 세션 자체가 바뀜었으면 True."""
        if session is None or session.identity is None:
            return False

        previous = self._session
        session = session.merged_with(previous)

        if previous is not None and previous.identity == session.identity:
            self._session = session
            if session.status and session.status != previous.status:
                logger.info(
                    "세션 상태 변경(%s): %s %s -> %s",
                    source,
                    session.label,
                    previous.status or "?",
                    session.status,
                )
                self._hooks.on_session_status(previous, session)
            return False

        self._session = session
        logger.info(
            "새 세션 감지(%s): %s [%s]",
            source,
            session.label,
            session.status or "?",
        )
        self._hooks.on_session_change(previous, session)
        return True

    # ---- 루프 ----

    def tick(self) -> None:
        """한 번의 감시 사이클. 예외를 밖으로 흘리지 않는다."""
        try:
            self._tick()
        except Exception:  # noqa: BLE001
            logger.exception("supervisor tick 중 예외(계속 진행)")

    def _tick(self) -> None:
        now = self._clock()

        if now - self._last_poll_at >= self._poll_interval():
            self._last_poll_at = now
            changed = self.observe_session(self._source.poll(), "static")
            if changed:
                # 새 세션은 새 연결로 — 깨끗한 스냅샷부터 다시 받는다.
                if self._client.connected:
                    self._reconnect("세션 변경")
                else:
                    self._try_connect(now)
                return

        if not self._client.connected:
            self._try_connect(now)
            return

        self._check_health(now)

    def _poll_interval(self) -> float:
        if self._session is not None and self._session.is_running:
            return self._config.live_poll_interval
        return self._config.poll_interval

    def _try_connect(self, now: float) -> None:
        if now < self._next_attempt_at:
            return
        if self._client.connect():
            self._attempts = 0
            self._next_attempt_at = 0.0
            self._connected_at = self._clock()
            first = not self._ever_connected
            self._ever_connected = True
            self._hooks.on_connected(first)
            return

        backoff = self._config.backoff
        delay = backoff[min(self._attempts, len(backoff) - 1)]
        self._attempts += 1
        self._next_attempt_at = self._clock() + delay
        logger.warning("연결 실패(%d회째) — %s초 뒤 재시도", self._attempts, delay)

    def _check_health(self, now: float) -> None:
        silent_for = now - max(self._client.last_message_at, self._connected_at)
        running = self._session is not None and self._session.is_running

        if running and silent_for > self._config.silence_timeout:
            self._reconnect(f"세션 진행 중인데 {int(silent_for)}초간 데이터 없음")
            return

        if not running and now - self._connected_at > self._config.max_connection_age:
            self._reconnect("연결 주기적 재활용")

    def _reconnect(self, reason: str) -> None:
        logger.info("재연결: %s", reason)
        self._client.close()
        self._attempts = 0
        self._next_attempt_at = 0.0
        self._try_connect(self._clock())
