"""순위(포지션) 변동 추적기.

F1 라이브타이밍 데이터:
  - DriverList: {"<번호>": {"Tla": "VER", "RacingNumber": "3", ...}}  번호->약어 매핑
  - TimingData.Lines["<번호>"]["Position"]: 현재 순위(문자열)

TimingData feed(변경분)는 부분 업데이트만 온다. 순위가 바뀐 경우에만
해당 드라이버 라인에 "Position" 키가 포함되므로, 그것만 보고 변동을 감지한다.
(섹터/갭 업데이트가 대부분이고 거기엔 Position이 없다.)
"""
from typing import NamedTuple


class PositionChange(NamedTuple):
    tla: str          # 드라이버 약어 (예: VER)
    number: str       # 레이싱 넘버 (예: 3)
    old_pos: int      # 이전 순위
    new_pos: int      # 새 순위

    @property
    def gained(self) -> bool:
        """순위가 올라갔는지(숫자가 작아졌는지)."""
        return self.new_pos < self.old_pos


class DriverTracker:
    """드라이버 약어 매핑과 마지막 순위를 들고 있다가 변동을 계산한다."""

    def __init__(self) -> None:
        self._tla: dict[str, str] = {}        # 번호 -> 약어
        self._positions: dict[str, int] = {}  # 번호 -> 현재 순위

    # ---- DriverList ----
    def update_driver_list(self, content: object) -> None:
        """DriverList 스냅샷/변경분으로 번호->약어 매핑을 갱신한다."""
        if not isinstance(content, dict):
            return
        for number, info in content.items():
            if isinstance(info, dict):
                tla = info.get("Tla")
                if tla:
                    self._tla[str(number)] = tla

    def _name(self, number: str) -> str:
        return self._tla.get(str(number), f"#{number}")

    # ---- TimingData ----
    def apply_timing(self, content: object, *, is_snapshot: bool) -> list[PositionChange]:
        """TimingData를 적용하고, 발생한 순위 변동 목록을 반환한다.

        스냅샷일 때는 기준선만 세우고 변동으로 보고하지 않는다(빈 리스트).
        """
        changes: list[PositionChange] = []
        if not isinstance(content, dict):
            return changes
        lines = content.get("Lines")
        if not isinstance(lines, dict):
            return changes

        for number, line in lines.items():
            if not isinstance(line, dict):
                continue
            pos_raw = line.get("Position")
            if pos_raw is None:
                continue  # 이 업데이트엔 순위 정보 없음 (섹터/갭 등)
            try:
                new_pos = int(pos_raw)
            except (ValueError, TypeError):
                continue

            number = str(number)
            old_pos = self._positions.get(number)
            self._positions[number] = new_pos

            if is_snapshot or old_pos is None or old_pos == new_pos:
                continue

            changes.append(
                PositionChange(
                    tla=self._name(number),
                    number=number,
                    old_pos=old_pos,
                    new_pos=new_pos,
                )
            )
        return changes
