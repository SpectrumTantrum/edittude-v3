from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from edittude_v3.media.core import ASPECTS, LOOKS, MediaError, read_json, write_json

HANDLE_IN = 0.12
HANDLE_OUT = 0.08
MAX_HOLD = 5.2
MIN_HOLD = 1.05
SCREENY_CAP = 4.0


@dataclass
class Event:
    src: str
    in_point: float
    out_point: float
    reason: str = ""

    def duration(self) -> float:
        return max(0.0, self.out_point - self.in_point)

    def to_dict(self) -> dict[str, Any]:
        return {
            "src": self.src,
            "in": round(self.in_point, 3),
            "out": round(self.out_point, 3),
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Event:
        return cls(
            src=str(data["src"]),
            in_point=float(data.get("in") or data.get("in_point") or 0),
            out_point=float(data.get("out") or data.get("out_point") or 0),
            reason=str(data.get("reason") or ""),
        )


@dataclass
class EditDecision:
    events: list[Event] = field(default_factory=list)
    title: str = ""
    subtitle: str = ""
    aspect: str = "16:9"
    look: str = "warm"
    fps: int = 30
    voiceover: str | None = None
    music: str | None = None
    notes: str = ""

    @property
    def width(self) -> int:
        return ASPECTS.get(self.aspect, ASPECTS["16:9"])[0]

    @property
    def height(self) -> int:
        return ASPECTS.get(self.aspect, ASPECTS["16:9"])[1]

    def duration(self) -> float:
        return sum(event.duration() for event in self.events)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "title": self.title,
            "subtitle": self.subtitle,
            "aspect": self.aspect,
            "look": self.look,
            "fps": self.fps,
            "width": self.width,
            "height": self.height,
            "voiceover": self.voiceover,
            "music": self.music,
            "duration": round(self.duration(), 3),
            "notes": self.notes,
            "events": [event.to_dict() for event in self.events],
        }


def load_edl(path: Path) -> EditDecision:
    data = read_json(path)
    return edl_from_dict(data)


def edl_from_dict(data: dict[str, Any]) -> EditDecision:
    if not isinstance(data, dict):
        raise MediaError("EDL must be an object")
    items = data.get("events", [])
    if not isinstance(items, list):
        raise MediaError("EDL events must be a list")
    events = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise MediaError(f"EDL event {index}: must be an object")
        for name in ("src", "in", "out"):
            if name not in item and (name == "src" or f"{name}_point" not in item):
                raise MediaError(f"EDL event {index}: missing {name}")
        event = Event.from_dict(item)
        if event.in_point >= event.out_point:
            raise MediaError(f"EDL event {index}: in must be less than out")
        events.append(event)
    return EditDecision(
        events=events,
        title=str(data.get("title") or ""),
        subtitle=str(data.get("subtitle") or ""),
        aspect=str(data.get("aspect") or "16:9"),
        look=str(data.get("look") or "warm"),
        fps=int(data.get("fps") or 30),
        voiceover=data.get("voiceover"),
        music=data.get("music"),
        notes=str(data.get("notes") or ""),
    )


def save_edl(edl: EditDecision, path: Path) -> Path:
    return write_json(path, edl.to_dict())


def pick_voiceover(inventory: dict[str, Any]) -> dict[str, Any] | None:
    audios = [c for c in inventory.get("clips") or [] if c.get("kind") == "audio"]
    if not audios:
        return None
    ranked = sorted(audios, key=lambda c: (_vo_score(c), c.get("duration") or 0), reverse=True)
    return ranked[0]


def pick_music(inventory: dict[str, Any], voiceover: dict[str, Any] | None) -> dict[str, Any] | None:
    audios = [c for c in inventory.get("clips") or [] if c.get("kind") == "audio"]
    vo_path = voiceover.get("path") if voiceover else None
    candidates = []
    for clip in audios:
        if clip.get("path") == vo_path:
            continue
        name = clip.get("name", "").lower()
        if any(token in name for token in ("music", "bed", "underscore", "song", "score")):
            candidates.append(clip)
    if not candidates:
        return None
    return max(candidates, key=lambda c: c.get("duration") or 0)


def _vo_score(clip: dict[str, Any]) -> int:
    name = clip.get("name", "").lower()
    score = 0
    for token in ("voiceover", "voice-over", "narration", "vo "):
        if token in name:
            score += 10
    if name.endswith(".m4a") or name.endswith(".wav"):
        score += 1
    return score


def first_cut(
    inventory: dict[str, Any],
    *,
    title: str = "",
    aspect: str = "16:9",
    look: str = "warm",
    target: float | None = None,
) -> EditDecision:
    videos = [c for c in inventory.get("clips") or [] if c.get("kind") == "video"]
    videos = sorted(videos, key=lambda c: (c.get("creation_time") or "", c.get("name") or ""))
    voiceover = pick_voiceover(inventory)
    music = pick_music(inventory, voiceover)
    if target is None:
        if voiceover:
            target = float(voiceover["duration"])
        else:
            total = sum(float(c.get("duration") or 0) for c in videos)
            target = min(60.0, max(12.0, total * 0.72))

    events: list[Event] = []
    count = len(videos)
    for index, clip in enumerate(videos):
        src_dur = float(clip.get("duration") or 0)
        if src_dur < 0.4:
            continue
        start = HANDLE_IN if src_dur > HANDLE_IN + MIN_HOLD else 0.0
        end = src_dur - HANDLE_OUT if src_dur - HANDLE_OUT > start + MIN_HOLD else src_dur
        hold = end - start
        cap = SCREENY_CAP if _looks_like_screen(clip) else MAX_HOLD
        if hold > cap:
            end = start + cap
        events.append(
            Event(
                src=clip["path"],
                in_point=start,
                out_point=end,
                reason=_reason(index, count, clip),
            )
        )

    fit_to_duration(events, target)
    notes = (
        "Chronology is the spine. Handles off iPhone starts. "
        "Long holds capped. Picture fitted to voiceover when one exists."
    )
    return EditDecision(
        events=events,
        title=title,
        aspect=aspect if aspect in ASPECTS else "16:9",
        look=look if look in LOOKS else "warm",
        voiceover=voiceover["path"] if voiceover else None,
        music=music["path"] if music else None,
        notes=notes,
    )


def fit_to_duration(events: list[Event], target: float) -> list[Event]:
    if not events or target <= 0:
        return events
    current = sum(event.duration() for event in events)
    if abs(current - target) < 0.15:
        return events
    if current > target:
        _shrink(events, current - target)
    else:
        _grow(events, target - current)
    return events


def tighten(edl: EditDecision, *, drop_longest: bool = False) -> EditDecision:
    events = [
        Event(event.src, event.in_point, event.out_point, event.reason) for event in edl.events
    ]
    if drop_longest and len(events) > 4:
        longest = max(range(len(events)), key=lambda i: events[i].duration())
        if events[longest].duration() > 3.5 and 0 < longest < len(events) - 1:
            events.pop(longest)
    for event in events:
        hold = event.duration()
        if hold > 3.4:
            event.out_point = event.in_point + max(MIN_HOLD, hold * 0.82)
    target = None
    if edl.voiceover:
        vo = Path(edl.voiceover)
        if vo.is_file():
            from edittude_v3.media.inventory import describe_clip

            target = float(describe_clip(vo)["duration"])
    if target:
        fit_to_duration(events, target)
    edl.events = events
    return edl


def _shrink(events: list[Event], excess: float) -> None:
    remaining = excess
    # Prefer trimming the longest interior holds first.
    order = sorted(range(len(events)), key=lambda i: events[i].duration(), reverse=True)
    for _ in range(12):
        if remaining <= 0.05:
            return
        progressed = False
        for index in order:
            event = events[index]
            slack = event.duration() - MIN_HOLD
            if slack <= 0.05:
                continue
            take = min(slack, remaining, max(0.12, slack * 0.35))
            event.out_point -= take
            remaining -= take
            progressed = True
            if remaining <= 0.05:
                return
        if not progressed:
            break
    # Last resort: drop a middle short insert if still long.
    while remaining > 0.8 and len(events) > 6:
        middles = list(range(1, len(events) - 1))
        victim = max(middles, key=lambda i: events[i].duration())
        remaining -= events[victim].duration()
        events.pop(victim)


def _grow(events: list[Event], need: float) -> None:
    remaining = need
    for event in events:
        if remaining <= 0.05:
            return
        src = Path(event.src)
        if not src.is_file():
            continue
        from edittude_v3.media.inventory import describe_clip

        src_dur = float(describe_clip(src)["duration"])
        slack = src_dur - HANDLE_OUT - event.out_point
        if slack <= 0.05:
            continue
        take = min(slack, remaining)
        event.out_point += take
        remaining -= take


def _looks_like_screen(clip: dict[str, Any]) -> bool:
    name = clip.get("name", "").lower()
    if any(token in name for token in ("screen", "laptop", "ui", "capture")):
        return True
    # Long static-ish clips in this set are usually screens.
    return float(clip.get("duration") or 0) >= 8.5


def _reason(index: int, count: int, clip: dict[str, Any]) -> str:
    name = clip.get("name", "")
    if index == 0:
        return f"open on {name}"
    if index == count - 1:
        return f"button / close {name}"
    if index == 1:
        return f"establish place {name}"
    if _looks_like_screen(clip):
        return f"detail, keep short {name}"
    if index < count * 0.35:
        return f"arrive {name}"
    if index < count * 0.7:
        return f"move {name}"
    return f"work / land {name}"


def as_plain(edl: EditDecision) -> dict[str, Any]:
    return asdict(edl)
