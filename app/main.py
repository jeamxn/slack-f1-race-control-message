"""엔트리포인트: F1 라이브타이밍에서 Race Control Message를 받아 Slack으로 알림.

한 번 띄우면 계속 돌아가는 게 목표다. 새 GP/세션은 supervisor 가 감지해서
상태를 리셋하고 다시 붙는다.

RaceControlMessages 페이로드 형태:
  - 스냅샷: {"Messages": [{...}, ...]} 또는 {"Messages": {"0": {...}}}
  - 변경분 푸시: {"Messages": {"5": {...}}}
둘 다 대응한다.

중복/과거 메시지 처리:
  - 같은 메시지를 두 번 보내지 않도록 Utc+Message 키로 중복 제거한다.
  - 구독 직후 내려오는 스냅샷에는 지나간 메시지가 통째로 들어 있으므로,
    SNAPSHOT_MAX_AGE 보다 오래된 건 보내지 않는다(최근 건은 보내서
    재연결 중 놓친 메시지를 따라잡는다).
"""
import logging
import os
import signal
import threading
from datetime import datetime, timezone

from .config import Config
from .driver_tracker import DriverTracker
from .f1_client import F1LiveClient
from .formatter import (
    format_highlight,
    format_message,
    format_pit_event,
    format_position_change,
)
from .session import SessionInfo, StaticSessionSource
from .slack_notifier import SlackNotifier
from .supervisor import Supervisor, SupervisorConfig, SupervisorHooks

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


def message_age_seconds(msg: dict, now: float = None) -> float:
    """메시지가 얼마나 오래됐는지(초). Utc 를 못 읽으면 무한대."""
    raw = msg.get("Utc")
    if not isinstance(raw, str) or not raw:
        return float("inf")
    text = raw.rstrip("Z")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return float("inf")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    reference = now if now is not None else datetime.now(timezone.utc).timestamp()
    return reference - parsed.timestamp()


class App:
    def __init__(self) -> None:
        Config.validate()
        self._slack = SlackNotifier(Config.SLACK_BOT_TOKEN, Config.SLACK_CHANNEL_ID)
        self._client = F1LiveClient(self._on_topic)
        self._tracker = DriverTracker()
        # 이미 처리한 메시지(중복 방지). 스냅샷과 변경분이 겹칠 수 있다.
        self._seen: set = set()
        # 연결 후 첫 페이로드는 '스냅샷'이다 (과거 메시지 필터 적용 대상).
        self._rcm_snapshot_done = False
        self._timing_snapshot_done = False

        self._supervisor = Supervisor(
            self._client,
            StaticSessionSource(),
            hooks=SupervisorHooks(
                on_session_change=self._on_session_change,
                on_session_status=self._on_session_status,
                on_connected=self._on_connected,
            ),
            config=SupervisorConfig(
                poll_interval=Config.SESSION_POLL_INTERVAL,
                live_poll_interval=Config.SESSION_POLL_INTERVAL_LIVE,
                silence_timeout=Config.SILENCE_TIMEOUT,
                max_connection_age=Config.MAX_CONNECTION_AGE,
            ),
        )

    # ---- supervisor 콜백 ----

    def _on_session_change(self, previous, current) -> None:
        """세션이 바뀌면 누적 상태를 다 버린다(순위·타이어·중복키)."""
        self._seen.clear()
        self._tracker = DriverTracker()
        self._timing_snapshot_done = False
        self._rcm_snapshot_done = False
        if Config.ANNOUNCE_SESSION and (previous is not None or current.is_running):
            self._slack.send(f"🏁 *{current.label}* — 라이브 타이밍 감시 시작")

    def _on_session_status(self, previous, current) -> None:
        if not Config.ANNOUNCE_SESSION:
            return
        if current.is_running and not previous.is_running:
            self._slack.send(f"🟢 *{current.label}* 세션 시작")
        elif current.is_closed and not previous.is_closed:
            self._slack.send(f"⏹️ *{current.label}* 세션 종료")

    def _on_connected(self, first: bool) -> None:
        # 새 연결이면 전체 스냅샷이 다시 온다.
        self._rcm_snapshot_done = False
        self._timing_snapshot_done = False
        if not first:
            logger.info("재연결 완료 — 구독 다시 마쳐다.")

    # ---- 피드 처리 ----

    def _dedup_key(self, key: str, msg: dict) -> str:
        # Utc + Message 조합이 가장 신뢰성 있는 고유키.
        return f"{msg.get('Utc', key)}|{msg.get('Message', '')}"

    def _on_topic(self, topic: str, content: object) -> None:
        if topic == "SessionInfo":
            info = SessionInfo.from_payload(content)
            if info is not None:
                self._supervisor.observe_session(info, "feed")
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
        self._rcm_snapshot_done = True
        for key, msg in _iter_messages(content):
            dk = self._dedup_key(key, msg)
            if dk in self._seen:
                continue
            self._seen.add(dk)

            # 스냅샷에 섮여 오는 지나간 메시지는 알리지 않는다.
            if (
                is_snapshot
                and not Config.NOTIFY_SNAPSHOT
                and message_age_seconds(msg) > Config.SNAPSHOT_MAX_AGE
            ):
                logger.info("[지나간 메시지 생략] %s", msg.get("Message", ""))
                continue

            text = format_message(msg)
            ok = self._slack.send(text)
            logger.info("%s %s", "OK" if ok else "FAIL", text)

    def _handle_timing(self, content: object) -> None:
        is_snapshot = not self._timing_snapshot_done
        changes = self._tracker.apply_timing(content, is_snapshot=is_snapshot)
        highlights = self._tracker.check_highlights(content, is_snapshot=is_snapshot)
        pit_events = self._tracker.check_pits(content, is_snapshot=is_snapshot)
        if is_snapshot:
            self._timing_snapshot_done = True

        # 순위 변동은 @here 없이 전송 (너무 잘아서 멘션 폭탄 방지).
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

    # ---- 실행 ----

    def _heartbeat(self) -> None:
        path = Config.HEALTH_FILE
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fp:
                fp.write(str(os.getpid()))
        except OSError:
            pass

    def run(self) -> None:
        bot_name = self._slack.check_auth()
        logger.info(
            "Slack 인증 성공 (bot=%s). 채널=%s", bot_name, Config.SLACK_CHANNEL_ID
        )
        logger.info("F1 라이브타이밍 감시 시작...")

        stop = threading.Event()

        def _shutdown(signum, _frame):
            logger.info("종료 신호(%s) 수신. 정리 중...", signum)
            stop.set()

        signal.signal(signal.SIGINT, _shutdown)
        signal.signal(signal.SIGTERM, _shutdown)

        try:
            while not stop.is_set():
                self._supervisor.tick()
                self._heartbeat()
                stop.wait(Config.TICK_INTERVAL)
        finally:
            self._client.close()


def main() -> None:
    App().run()


if __name__ == "__main__":
    main()
