"""Race Control Message를 Slack용 텍스트로 포맷팅."""
from typing import Any

# 플래그별 이모지 매핑
FLAG_EMOJI = {
    "GREEN": ":green_circle:",
    "YELLOW": ":large_yellow_circle:",
    "DOUBLE YELLOW": ":large_yellow_circle::large_yellow_circle:",
    "RED": ":red_circle:",
    "BLUE": ":large_blue_circle:",
    "CHEQUERED": ":checkered_flag:",
    "CLEAR": ":white_circle:",
    "BLACK AND WHITE": ":chequered_flag:",
}

# 카테고리별 이모지
CATEGORY_EMOJI = {
    "SafetyCar": ":rotating_light:",
    "Flag": ":triangular_flag_on_post:",
    "Drs": ":dash:",
    "CarEvent": ":racing_car:",
    "Other": ":loudspeaker:",
}


def _prefix(msg: dict[str, Any]) -> str:
    """메시지 종류에 맞는 선두 이모지를 고른다."""
    flag = (msg.get("Flag") or "").upper()
    if flag in FLAG_EMOJI:
        return FLAG_EMOJI[flag]
    category = msg.get("Category") or "Other"
    return CATEGORY_EMOJI.get(category, ":loudspeaker:")


def format_message(msg: dict[str, Any]) -> str:
    """단일 Race Control Message dict -> Slack 텍스트 한 줄."""
    prefix = _prefix(msg)
    parts: list[str] = []

    # 세션 시계(있으면)
    lap = msg.get("Lap")
    if lap is not None:
        parts.append(f"*LAP {lap}*")

    # 플래그 / 카테고리 라벨
    flag = msg.get("Flag")
    category = msg.get("Category")
    scope = msg.get("Scope")
    label_bits = [b for b in (flag, category, scope) if b and b != "Other"]
    # 중복 제거하며 순서 유지
    seen = set()
    label = " · ".join(
        b for b in label_bits if not (b in seen or seen.add(b))
    )
    if label:
        parts.append(f"[{label}]")

    # 실제 메시지 본문
    text = (msg.get("Message") or "").strip()
    parts.append(text)

    body = " ".join(p for p in parts if p)
    return f"{prefix} {body}".strip()
