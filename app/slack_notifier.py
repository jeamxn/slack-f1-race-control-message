"""Slack 메시지 전송 래퍼."""
import logging

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

logger = logging.getLogger(__name__)


class SlackNotifier:
    """Slack 채널로 메시지를 보내는 단순 래퍼 (chat.postMessage)."""

    def __init__(self, bot_token: str, channel_id: str) -> None:
        self._client = WebClient(token=bot_token)
        self._channel = channel_id

    def send(self, text: str) -> bool:
        """텍스트 한 건 전송. 성공 여부 반환."""
        try:
            self._client.chat_postMessage(
                channel=self._channel,
                text=text,
                unfurl_links=False,
                unfurl_media=False,
            )
            return True
        except SlackApiError as e:
            logger.error("Slack 전송 실패: %s", e.response.get("error", e))
            return False

    def check_auth(self) -> str:
        """토큰 유효성 확인. 봇 이름 반환, 실패 시 예외."""
        resp = self._client.auth_test()
        return resp.get("user", "unknown")
