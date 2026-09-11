"""환경변수 설정 로딩."""
import os

from dotenv import load_dotenv

load_dotenv()


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


class Config:
    """런타임 설정. .env 또는 환경변수에서 읽는다."""

    SLACK_BOT_TOKEN: str = os.getenv("SLACK_BOT_TOKEN", "")
    SLACK_APP_TOKEN: str = os.getenv("SLACK_APP_TOKEN", "")
    SLACK_CHANNEL_ID: str = os.getenv("SLACK_CHANNEL_ID", "")

    # 시작 시점의 스냅샷(이미 지나간 메시지)을 Slack으로 보낼지 여부.
    # 기본 False — 봇 켜진 이후 새로 들어오는 메시지만 알린다.
    NOTIFY_SNAPSHOT: bool = _bool("NOTIFY_SNAPSHOT", False)

    # 구독 직후 스냅샷에 섮여 오는 메시지 중 이 나이(초)보다 오래된 건 생략.
    # 재연결 중 놓친 최근 메시지는 살리고, 지나간 세션 로그는 안 보내기 위함.
    SNAPSHOT_MAX_AGE: float = _float("SNAPSHOT_MAX_AGE", 600.0)

    # 세션 시작/종료를 Slack에 알릴지 여부.
    ANNOUNCE_SESSION: bool = _bool("ANNOUNCE_SESSION", True)

    # 감시 루프 주기(초).
    TICK_INTERVAL: float = _float("TICK_INTERVAL", 5.0)
    # 현재 세션 확인(정적 SessionInfo.json) 주기(초).
    SESSION_POLL_INTERVAL: float = _float("SESSION_POLL_INTERVAL", 60.0)
    SESSION_POLL_INTERVAL_LIVE: float = _float("SESSION_POLL_INTERVAL_LIVE", 30.0)
    # 세션 진행 중인데 이만큼 데이터가 안 오면 재연결(초).
    SILENCE_TIMEOUT: float = _float("SILENCE_TIMEOUT", 180.0)
    # 세션이 없는 동안 연결을 통째로 재활용하는 주기(초).
    MAX_CONNECTION_AGE: float = _float("MAX_CONNECTION_AGE", 3600.0)

    # 살아있음 표시용 파일(도커 HEALTHCHECK에서 사용). 비워두면 비활성.
    HEALTH_FILE: str = os.getenv("HEALTH_FILE", "/tmp/f1-bot-alive")

    @classmethod
    def validate(cls) -> None:
        """필수 설정이 비어있으면 예외."""
        missing = [
            name
            for name in ("SLACK_BOT_TOKEN", "SLACK_CHANNEL_ID")
            if not getattr(cls, name)
        ]
        if missing:
            raise RuntimeError(
                f"필수 환경변수가 비어있습니다: {', '.join(missing)}. "
                ".env 파일을 확인하세요 (.env.example 참고)."
            )
