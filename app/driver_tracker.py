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
    team: str         # 팀 이름 (예: Red Bull Racing)
    old_pos: int      # 이전 순위
    new_pos: int      # 새 순위

    @property
    def gained(self) -> bool:
        """순위가 올라갔는지(숫자가 작아졌는지)."""
        return self.new_pos < self.old_pos


class LapHighlight(NamedTuple):
    kind: str         # "fastest_lap" | "purple_sector"
    tla: str
    number: str
    team: str
    value: str        # 랩타임 또는 섹터타임 문자열
    sector: int = 0   # purple_sector일 때 1/2/3 (fastest_lap은 0)


class PitEvent(NamedTuple):
    kind: str         # "pit_in" | "pit_out"
    tla: str
    number: str
    team: str
    position: int     # 현재 순위 (모르면 0)
    compound: str = ""  # pit_out일 때 새 타이어 컴파운드 (SOFT/MEDIUM/HARD 등)
    is_new: bool = False  # 새 타이어 여부


class DriverTracker:
    """드라이버 약어 매핑과 마지막 순위를 들고 있다가 변동을 계산한다."""

    def __init__(self) -> None:
        self._tla: dict[str, str] = {}        # 번호 -> 약어
        self._team: dict[str, str] = {}       # 번호 -> 팀 이름
        self._positions: dict[str, int] = {}  # 번호 -> 현재 순위
        # 하이라이트 중복 발사 방지용 마지막 발사 키
        self._last_fastest: str = ""          # "번호|랩타임"
        self._last_purple: dict[str, str] = {}  # "번호|섹터" -> 섹터타임
        # 피트/타이어 상태
        self._in_pit: dict[str, bool] = {}    # 번호 -> 현재 InPit 상태
        self._tyre: dict[str, tuple[str, bool]] = {}  # 번호 -> (컴파운드, 새타이어여부)

    # ---- DriverList ----
    def update_driver_list(self, content: object) -> None:
        """DriverList 스냅샷/변경분으로 번호->약어/팀 매핑을 갱신한다."""
        if not isinstance(content, dict):
            return
        for number, info in content.items():
            if isinstance(info, dict):
                tla = info.get("Tla")
                if tla:
                    self._tla[str(number)] = tla
                team = info.get("TeamName")
                if team:
                    self._team[str(number)] = team

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
                    team=self._team.get(number, ""),
                    old_pos=old_pos,
                    new_pos=new_pos,
                )
            )
        return changes

    # ---- Fastest lap / purple sectors ----
    def check_highlights(
        self, content: object, *, is_snapshot: bool
    ) -> list[LapHighlight]:
        """TimingData에서 세션 베스트랩 갱신과 퍼플 섹터를 감지한다.

        - fastest_lap: LastLapTime(또는 BestLapTime)에 OverallFastest=True
        - purple_sector: Sectors[i]에 OverallFastest=True

        스냅샷은 기준선만 세우고(중복 키 등록) 보고하지 않는다.
        """
        highlights: list[LapHighlight] = []
        if not isinstance(content, dict):
            return highlights
        lines = content.get("Lines")
        if not isinstance(lines, dict):
            return highlights

        for number, line in lines.items():
            if not isinstance(line, dict):
                continue
            number = str(number)

            # --- 세션 베스트랩 (오버롤 패스티스트) ---
            for key in ("LastLapTime", "BestLapTime"):
                lap = line.get(key)
                if isinstance(lap, dict) and lap.get("OverallFastest") is True:
                    value = (lap.get("Value") or "").strip()
                    if value:
                        fire_key = f"{number}|{value}"
                        if self._last_fastest != fire_key:
                            self._last_fastest = fire_key
                            if not is_snapshot:
                                highlights.append(
                                    LapHighlight(
                                        kind="fastest_lap",
                                        tla=self._name(number),
                                        number=number,
                                        team=self._team.get(number, ""),
                                        value=value,
                                    )
                                )
                    break  # LastLapTime 우선, 있으면 BestLapTime 안 봄

            # --- 퍼플 섹터 ---
            for sec_idx, sector in self._iter_sectors(line.get("Sectors")):
                if not isinstance(sector, dict):
                    continue
                if sector.get("OverallFastest") is True:
                    value = (sector.get("Value") or "").strip()
                    fire_key = f"{number}|{sec_idx}"
                    if self._last_purple.get(fire_key) != value:
                        self._last_purple[fire_key] = value
                        if not is_snapshot:
                            highlights.append(
                                LapHighlight(
                                    kind="purple_sector",
                                    tla=self._name(number),
                                    number=number,
                                    team=self._team.get(number, ""),
                                    value=value,
                                    sector=sec_idx + 1,  # 0-indexed -> 1/2/3
                                )
                            )
        return highlights

    @staticmethod
    def _iter_sectors(sectors: object):
        """Sectors를 (인덱스, 섹터dict)로 순회. 스냅샷=list, feed=dict 둘 다 처리."""
        if isinstance(sectors, list):
            for i, sec in enumerate(sectors):
                yield i, sec
        elif isinstance(sectors, dict):
            for k, sec in sectors.items():
                try:
                    yield int(k), sec
                except (ValueError, TypeError):
                    continue

    # ---- 타이어 (TimingAppData) ----
    def update_tyres(self, content: object) -> None:
        """TimingAppData의 Stints에서 각 드라이버의 현재(마지막) 컴파운드를 저장한다.

        Stints는 스냅샷=list, feed=dict("0","1",...) 둘 다 온다. 부분 업데이트는
        해당 스틴트만 오므로, 컴파운드/New가 들어온 경우에만 갱신한다.
        """
        if not isinstance(content, dict):
            return
        lines = content.get("Lines")
        if not isinstance(lines, dict):
            return
        for number, line in lines.items():
            if not isinstance(line, dict):
                continue
            stints = line.get("Stints")
            number = str(number)
            for _idx, stint in self._iter_sectors(stints):
                if not isinstance(stint, dict):
                    continue
                compound = stint.get("Compound")
                if compound:
                    is_new = str(stint.get("New", "")).lower() == "true"
                    self._tyre[number] = (compound, is_new)

    # ---- 피트 인/아웃 (TimingData) ----
    def check_pits(self, content: object, *, is_snapshot: bool) -> list[PitEvent]:
        """TimingData의 InPit/PitOut 전환을 감지한다.

        InPit이 False->True 가 되면 pit_in, True->False 가 되면 pit_out.
        스냅샷은 기준 상태만 세우고 보고하지 않는다.
        """
        events: list[PitEvent] = []
        if not isinstance(content, dict):
            return events
        lines = content.get("Lines")
        if not isinstance(lines, dict):
            return events

        for number, line in lines.items():
            if not isinstance(line, dict):
                continue
            if "InPit" not in line:
                continue  # 피트 상태 변화 없는 업데이트
            number = str(number)
            new_in_pit = bool(line.get("InPit"))
            old_in_pit = self._in_pit.get(number)
            self._in_pit[number] = new_in_pit

            if is_snapshot or old_in_pit is None or old_in_pit == new_in_pit:
                continue

            pos = self._positions.get(number, 0)
            if new_in_pit:
                events.append(
                    PitEvent(
                        kind="pit_in",
                        tla=self._name(number),
                        number=number,
                        team=self._team.get(number, ""),
                        position=pos,
                    )
                )
            else:
                compound, is_new = self._tyre.get(number, ("", False))
                events.append(
                    PitEvent(
                        kind="pit_out",
                        tla=self._name(number),
                        number=number,
                        team=self._team.get(number, ""),
                        position=pos,
                        compound=compound,
                        is_new=is_new,
                    )
                )
        return events
