"""엔트리포인트: F1 라이브타이밍에서 Race Control Message를 받아 Slack으로 알림.

RaceControlMessages 페이로드 형태:
  - 초기 스냅샷: {"Messages": {"0": {...}, "1": {...}}}  (dict, 인덱스 키)
  - 변경분 푸시: {"Messages": [{...}]} 또는 {"Messages": {"5": {...}}}
둘 다 대응한다.
"""
import logging
import signal
import threading

from .config import Config
from .driver_tracker import DriverTracker
from .f1_client import F1LiveClient
from .formatter import (
    format_highlight,
    format_message,
    format_pit_event,
    format_position_change,
)
from .slack_notifier import SlackNotifier

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("slack-f1")


def _iter_messages(content: object):
    """RaceControlMessages content에서 개별 메시지 dict들을 뽑아낸다."""
    if not isinstance(content, dict):
        return
    messages = content.get("Messages")
    if isinstance(messages, dict):
        # 인덱스 키 순서대로 정렬 (문자열 숫자 키)
        for key in sorted(messages, key=lambda k: int(k) if str(k).isdigit() else k):
            item = messages[key]
            if isinstance(item, dict):
                yield key, item
    elif isinstance(messages, list):
        for idx, item in enumerate(messages):
            if isinstance(item, dict):
                yield str(idx), item


class App:
    def __init__(self) -> None:
        Config.validate()
        self._slack = SlackNotifier(Config.SLACK_BOT_TOKEN, Config.SLACK_CHANNEL_ID)
        self._client = F1LiveClient(self._on_topic)
        self._tracker = DriverTracker()
        # 이미 처리한 메시지(중복 방지). 스냅샷과 변경분이 겹칠 수 있다.
        self._seen: set[str] = set()
        self._rcm_snapshot_done = False
        self._timing_snapshot_done = False

    def _dedup_key(self, key: str, msg: dict) -> str:
        # Utc + Message 조합이 가장 신뢰성 있는 고유키.
        return f"{msg.get('Utc', key)}|{msg.get('Message', '')}"

    def _on_topic(self, topic: str, content: object) -> None:
        if topic == "SessionInfo" and isinstance(content, dict):
            meeting = content.get("Meeting", {})
            name = content.get("Name", "")
            logger.info(
                "세션: %s - %s", meeting.get("Name", "?"), name or "?"
            )
            return

        if topic == "DriverList":
            self._tracker.update_driver_list(content)
            return

        if topic == "TimingAppData":
            self._tracker.update_tyres(content)
            return

        if topic == "TimingData":
            self._handle_timing(content)
            return

        if topic == "RaceControlMessages":
            self._handle_race_control(content)
            return

    def _handle_race_control(self, content: object) -> None:
        is_snapshot = not self._rcm_snapshot_done
        for key, msg in _iter_messages(content):
            dk = self._dedup_key(key, msg)
            if dk in self._seen:
                continue
            self._seen.add(dk)

            # 스냅샷(봇 켜기 전 메시지)은 기본적으로 Slack 전송 생략.
            if is_snapshot and not Config.NOTIFY_SNAPSHOT:
                logger.info("[스냅샷 생략] %s", msg.get("Message", ""))
                continue

            text = format_message(msg)
            ok = self._slack.send(text)
            logger.info("%s %s", "OK" if ok else "FAIL", text)

        if is_snapshot:
            self._rcm_snapshot_done = True

    def _handle_timing(self, content: object) -> None:
        is_snapshot = not self._timing_snapshot_done
        changes = self._tracker.apply_timing(content, is_snapshot=is_snapshot)
        highlights = self._tracker.check_highlights(content, is_snapshot=is_snapshot)
        pit_events = self._tracker.check_pits(content, is_snapshot=is_snapshot)
        if is_snapshot:
            self._timing_snapshot_done = True

        # 순위 변동은 @here 없이 전송 (너무 잦아서 멘션 폭탄 방지).
        for change in changes:
            text = format_position_change(change)
            ok = self._slack.send(text)
            logger.info("%s %s", "OK" if ok else "FAIL", text)

        # 패스티스트 랩 / 퍼플 섹터 하이라이트.
        for h in highlights:
            text = format_highlight(h)
            ok = self._slack.send(text)
            logger.info("%s %s", "OK" if ok else "FAIL", text)

        # 피트 인/아웃.
        for e in pit_events:
            text = format_pit_event(e)
            ok = self._slack.send(text)
            logger.info("%s %s", "OK" if ok else "FAIL", text)

    def run(self) -> None:
        bot_name = self._slack.check_auth()
        logger.info("Slack 인증 성공 (bot=%s). 채널=%s", bot_name, Config.SLACK_CHANNEL_ID)
        logger.info("F1 라이브타이밍 연결 시작...")

        stop = threading.Event()

        def _shutdown(signum, _frame):
            logger.info("종료 신호(%s) 수신. 정리 중...", signum)
            stop.set()

        signal.signal(signal.SIGINT, _shutdown)
        signal.signal(signal.SIGTERM, _shutdown)

        # 클라이언트는 백그라운드 스레드에서 동작(논블로킹).
        # 자동 재연결이 켜져 있으므로 메인 스레드는 종료 신호만 기다린다.
        self._client.start()
        try:
            stop.wait()
        finally:
            self._client.stop()


def main() -> None:
    App().run()


if __name__ == "__main__":
    main()
