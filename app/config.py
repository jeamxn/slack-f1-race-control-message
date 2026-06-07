"""환경변수 설정 로딩."""
import os

from dotenv import load_dotenv

load_dotenv()


class Config:
    """런타임 설정. .env 또는 환경변수에서 읽는다."""

    SLACK_BOT_TOKEN: str = os.getenv("SLACK_BOT_TOKEN", "")
    SLACK_APP_TOKEN: str = os.getenv("SLACK_APP_TOKEN", "")
    SLACK_CHANNEL_ID: str = os.getenv("SLACK_CHANNEL_ID", "")

    # F1 라이브타이밍 SignalR 엔드포인트
    F1_SIGNALR_BASE: str = os.getenv(
        "F1_SIGNALR_BASE", "https://livetiming.formula1.com/signalr"
    )

    # 시작 시점의 스냅샷(이미 지나간 메시지)을 Slack으로 보낼지 여부.
    # 기본 False — 봇 켠 이후 새로 들어오는 메시지만 알린다.
    NOTIFY_SNAPSHOT: bool = os.getenv("NOTIFY_SNAPSHOT", "false").lower() == "true"

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
