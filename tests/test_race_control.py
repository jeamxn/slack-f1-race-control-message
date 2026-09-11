"""Race Control 메시지 처리(중복 제거·과거 메시지 생략·세션 리셋) 테스트."""
import os
import unittest
from datetime import datetime, timedelta, timezone

os.environ.setdefault("SLACK_BOT_TOKEN", "xoxb-test")
os.environ.setdefault("SLACK_CHANNEL_ID", "C0TEST")

from app.main import App, _iter_messages, message_age_seconds  # noqa: E402
from app.session import SessionInfo  # noqa: E402


def utc_ago(seconds):
    stamp = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    return stamp.strftime("%Y-%m-%dT%H:%M:%S")


OLD_A = {"Utc": "2026-01-01T11:30:00", "Category": "Flag", "Flag": "GREEN",
         "Scope": "Track", "Message": "GREEN LIGHT - PIT EXIT OPEN"}
OLD_B = {"Utc": "2026-01-01T11:40:00", "Category": "Other",
         "Message": "TRACK SURFACE SLIPPERY"}


class FakeSlack:
    def __init__(self) -> None:
        self.sent = []

    def send(self, text):
        self.sent.append(text)
        return True


def make_app():
    app = App()
    slack = FakeSlack()
    app._slack = slack
    return app, slack


def rcm(*messages):
    return {"Messages": list(messages)}


def fresh(message, age=5):
    return {"Utc": utc_ago(age), "Category": "SafetyCar", "Message": message}


class IterMessagesTest(unittest.TestCase):
    def test_dict_payload_is_sorted_numerically(self):
        content = {"Messages": {"10": {"Message": "ten"}, "2": {"Message": "two"}}}
        self.assertEqual(
            ["two", "ten"], [m["Message"] for _k, m in _iter_messages(content)]
        )

    def test_list_payload(self):
        self.assertEqual(2, len(list(_iter_messages(rcm(OLD_A, OLD_B)))))

    def test_garbage(self):
        self.assertEqual([], list(_iter_messages(None)))
        self.assertEqual([], list(_iter_messages({"Messages": 3})))


class MessageAgeTest(unittest.TestCase):
    def test_recent_message(self):
        self.assertLess(message_age_seconds({"Utc": utc_ago(10)}), 60)

    def test_old_message(self):
        self.assertGreater(message_age_seconds(OLD_A), 3600)

    def test_unparsable_is_infinite(self):
        self.assertEqual(float("inf"), message_age_seconds({}))
        self.assertEqual(float("inf"), message_age_seconds({"Utc": "어제"}))

    def test_trailing_z_is_handled(self):
        self.assertLess(message_age_seconds({"Utc": utc_ago(10) + "Z"}), 60)


class RaceControlTest(unittest.TestCase):
    def test_old_snapshot_is_skipped_then_new_messages_are_sent(self):
        app, slack = make_app()
        app._handle_race_control(rcm(OLD_A, OLD_B))
        self.assertEqual([], slack.sent)

        app._handle_race_control(rcm(OLD_A, OLD_B, fresh("SAFETY CAR DEPLOYED")))
        self.assertEqual(1, len(slack.sent))
        self.assertIn("SAFETY CAR DEPLOYED", slack.sent[0])

    def test_recent_message_in_snapshot_is_sent(self):
        """재연결 중 놓친 메시지는 스냅샷에서라도 살려야 한다."""
        app, slack = make_app()
        app._handle_race_control(rcm(OLD_A, fresh("RED FLAG", age=30)))
        self.assertEqual(1, len(slack.sent))
        self.assertIn("RED FLAG", slack.sent[0])

    def test_reconnect_snapshot_does_not_resend(self):
        app, slack = make_app()
        app._handle_race_control(rcm(OLD_A))
        sc = fresh("SAFETY CAR DEPLOYED")
        app._handle_race_control(rcm(OLD_A, sc))
        slack.sent.clear()

        app._on_connected(first=False)
        missed = fresh("VIRTUAL SAFETY CAR ENDING", age=3)
        app._handle_race_control(rcm(OLD_A, sc, missed))
        self.assertEqual(1, len(slack.sent))
        self.assertIn("VIRTUAL SAFETY CAR ENDING", slack.sent[0])

    def test_new_session_resets_dedup_but_keeps_recent_messages(self):
        app, slack = make_app()
        app._handle_race_control(rcm(OLD_A))
        slack.sent.clear()

        previous = SessionInfo(key=1, meeting="GP", name="Practice 1")
        current = SessionInfo(key=2, meeting="GP", name="Practice 2",
                              status="Inactive")
        app._on_session_change(previous, current)
        self.assertEqual(1, len(slack.sent))  # 세션 안내
        self.assertIn("Practice 2", slack.sent[0])
        slack.sent.clear()

        # 새 세션 첫 페이로드: 지나간 건은 무시, 방금 건은 전송.
        app._handle_race_control(rcm(OLD_A, fresh("GREEN LIGHT - PIT EXIT OPEN")))
        self.assertEqual(1, len(slack.sent))
        self.assertIn("GREEN LIGHT", slack.sent[0])

    def test_session_status_announcements(self):
        app, slack = make_app()
        inactive = SessionInfo(key=1, meeting="GP", name="Race", status="Inactive")
        started = SessionInfo(key=1, meeting="GP", name="Race", status="Started")
        finished = SessionInfo(key=1, meeting="GP", name="Race", status="Finalised")

        app._on_session_status(inactive, started)
        app._on_session_status(started, finished)
        self.assertEqual(2, len(slack.sent))
        self.assertIn("시작", slack.sent[0])
        self.assertIn("종료", slack.sent[1])

    def test_session_info_topic_feeds_supervisor(self):
        app, _slack = make_app()
        app._on_topic(
            "SessionInfo",
            {"Key": 777, "Name": "Qualifying", "Meeting": {"Name": "GP"},
             "SessionStatus": "Started"},
        )
        self.assertIsNotNone(app._supervisor.session)
        self.assertEqual(777, app._supervisor.session.key)


if __name__ == "__main__":
    unittest.main()
