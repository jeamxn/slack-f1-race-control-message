"""SessionInfo 파싱/판정 테스트."""
import unittest

from app.session import SessionInfo

STATIC_PAYLOAD = {
    "Meeting": {"Key": 1294, "Name": "Spanish Grand Prix", "Location": "Madrid"},
    "SessionStatus": "Finalised",
    "Key": 11362,
    "Type": "Practice",
    "Number": 1,
    "Name": "Practice 1",
    "Path": "2026/2026-09-13_Spanish_Grand_Prix/2026-09-11_Practice_1/",
}


class SessionInfoTest(unittest.TestCase):
    def test_parses_static_payload(self):
        info = SessionInfo.from_payload(STATIC_PAYLOAD, streaming="Available")
        self.assertIsNotNone(info)
        self.assertEqual(11362, info.key)
        self.assertEqual("Spanish Grand Prix", info.meeting)
        self.assertEqual("Practice 1", info.name)
        self.assertEqual("Spanish Grand Prix · Practice 1", info.label)
        self.assertTrue(info.is_closed)
        self.assertFalse(info.is_running)

    def test_running_status(self):
        info = SessionInfo.from_payload({**STATIC_PAYLOAD, "SessionStatus": "Started"})
        self.assertTrue(info.is_running)

    def test_rejects_garbage(self):
        self.assertIsNone(SessionInfo.from_payload(None))
        self.assertIsNone(SessionInfo.from_payload("nope"))
        self.assertIsNone(SessionInfo.from_payload({}))

    def test_identity_falls_back_to_path(self):
        info = SessionInfo.from_payload({"Path": "2026/x/", "Name": "Race"})
        self.assertEqual("2026/x/", info.identity)

    def test_merged_with_fills_blanks_for_same_session(self):
        previous = SessionInfo.from_payload(STATIC_PAYLOAD, streaming="Available")
        feed = SessionInfo.from_payload(
            {"Key": 11362, "Name": "Practice 1", "Meeting": {"Name": ""}}
        )
        merged = feed.merged_with(previous)
        self.assertEqual("Finalised", merged.status)
        self.assertEqual("Spanish Grand Prix", merged.meeting)

    def test_merged_with_ignores_other_session(self):
        previous = SessionInfo.from_payload(STATIC_PAYLOAD)
        other = SessionInfo.from_payload({"Key": 99999, "Name": "Qualifying"})
        merged = other.merged_with(previous)
        self.assertEqual("", merged.status)
        self.assertEqual(99999, merged.key)


if __name__ == "__main__":
    unittest.main()
