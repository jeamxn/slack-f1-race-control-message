"""현재 세션 정보 모델 + 정적 피드 조회.

F1 라이브타이밍은 두 곳에서 세션 정보를 준다.
  1. SignalR `SessionInfo` 토픽 (실시간 푸시, 연결되어 있을 때만)
  2. 정적 JSON https://livetiming.formula1.com/static/SessionInfo.json

소켓이 죽어 있으면 1번은 영영 안 오므로, "다음 GP가 시작됐다"는 걸
알아차리려면 2번을 주기적으로 폴링해야 한다. 두 경로 모두 같은
`SessionInfo` 데이터클래스로 파싱해서 supervisor 가 한 곳에서 판단한다.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, replace
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

STATIC_BASE = "https://livetiming.formula1.com/static"
SESSION_INFO_URL = f"{STATIC_BASE}/SessionInfo.json"
STREAMING_STATUS_URL = f"{STATIC_BASE}/StreamingStatus.json"

# SessionStatus 값: Inactive / Started / Aborted / Finished / Finalised / Ends
RUNNING_STATUSES = frozenset({"Started", "Aborted"})
CLOSED_STATUSES = frozenset({"Finished", "Finalised", "Ends"})


@dataclass(frozen=True)
class SessionInfo:
    """세션 하나. 두 경로(피드/정적)에서 동일하게 만들어진다."""

    key: Optional[int] = None
    meeting: str = ""
    name: str = ""
    type: str = ""
    status: str = ""
    path: str = ""
    streaming: str = ""

    @classmethod
    def from_payload(
        cls, payload: Any, streaming: str = ""
    ) -> Optional["SessionInfo"]:
        """SessionInfo 페이로드(dict) -> SessionInfo. 모양이 아니면 None."""
        if not isinstance(payload, dict):
            return None

        raw_key = payload.get("Key")
        try:
            key = int(raw_key) if raw_key is not None else None
        except (TypeError, ValueError):
            key = None

        meeting = payload.get("Meeting")
        meeting_name = ""
        if isinstance(meeting, dict):
            meeting_name = str(meeting.get("Name") or "")

        info = cls(
            key=key,
            meeting=meeting_name,
            name=str(payload.get("Name") or ""),
            type=str(payload.get("Type") or ""),
            status=str(payload.get("SessionStatus") or ""),
            path=str(payload.get("Path") or ""),
            streaming=streaming,
        )
        if info.identity is None:
            return None
        return info

    @property
    def identity(self) -> Optional[object]:
        """세션을 구분하는 값. Key 가 없으면 Path 로 대체."""
        if self.key is not None:
            return self.key
        return self.path or None

    @property
    def label(self) -> str:
        bits = [b for b in (self.meeting, self.name) if b]
        if bits:
            return " · ".join(bits)
        return f"세션 {self.identity}"

    @property
    def is_running(self) -> bool:
        """지금 주행 중인 세션인가(데이터가 계속 흘러야 정상)."""
        return self.status in RUNNING_STATUSES

    @property
    def is_closed(self) -> bool:
        return self.status in CLOSED_STATUSES

    def merged_with(self, previous: Optional["SessionInfo"]) -> "SessionInfo":
        """비어있는 필드를 이전 정보로 채운다.

        SignalR 피드의 SessionInfo 에는 SessionStatus 가 없을 수 있어서,
        같은 세션이면 직전 상태를 유지한다.
        """
        if previous is None or previous.identity != self.identity:
            return self
        return replace(
            self,
            status=self.status or previous.status,
            streaming=self.streaming or previous.streaming,
            meeting=self.meeting or previous.meeting,
            name=self.name or previous.name,
            path=self.path or previous.path,
            type=self.type or previous.type,
        )


class StaticSessionSource:
    """정적 JSON 에서 현재 세션을 읽는다. 실패하면 None 을 돌려준다."""

    def __init__(self, timeout: float = 10.0) -> None:
        self._timeout = timeout
        self._http = requests.Session()

    def _get_json(self, url: str) -> Any:
        # CloudFront 캐시를 피하기 위해 쿼리스트링을 붙인다.
        resp = self._http.get(
            url,
            params={"_": int(time.time() * 1000)},
            headers={"Cache-Control": "no-cache"},
            timeout=self._timeout,
        )
        resp.raise_for_status()
        # 이 파일들은 BOM(\ufeff) 이 붙어서 온다.
        return json.loads(resp.content.decode("utf-8-sig"))

    def streaming_status(self) -> str:
        try:
            data = self._get_json(STREAMING_STATUS_URL)
        except Exception as exc:  # noqa: BLE001
            logger.debug("StreamingStatus 조회 실패: %s", exc)
            return ""
        if isinstance(data, dict):
            return str(data.get("Status") or "")
        return ""

    def poll(self) -> Optional[SessionInfo]:
        try:
            data = self._get_json(SESSION_INFO_URL)
        except Exception as exc:  # noqa: BLE001
            logger.warning("SessionInfo 조회 실패: %s", exc)
            return None
        return SessionInfo.from_payload(data, streaming=self.streaming_status())
