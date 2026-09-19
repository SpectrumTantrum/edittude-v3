"""Local media operations. Times are source-relative seconds; outputs never overwrite."""
from __future__ import annotations

import array
from bisect import bisect_right
from collections import defaultdict, deque
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import re
import struct
import sys
import tempfile
import wave

from .common import artifact_path, input_path, output_path, probe, publish, run


def _number(value, name: str, minimum: float = 0, maximum: float = 1e9) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a number")
    try:
        value = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a number") from error
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _integer(value, name: str, minimum: int, maximum: int) -> int:
    number = _number(value, name, minimum, maximum)
    if not number.is_integer():
        raise ValueError(f"{name} must be an integer")
    return int(number)


def _stream(info: dict, kind: str, *, required: bool = True) -> dict:
    stream = next((item for item in info.get("streams", []) if item["codec_type"] == kind), None)
    if stream is None and required:
        raise ValueError(f"Input has no {kind} stream")
    return stream or {}


def _duration(info: dict, kind: str | None = None) -> float:
    stream = _stream(info, kind) if kind else {}
    value = stream.get("duration") or info.get("format", {}).get("duration")
    if value is None:
        raise ValueError("Media duration is unavailable")
    return _number(value, "duration", 1e-9)


def _info(root: Path, path: Path) -> dict:
    result = probe(path)
    result.setdefault("format", {})["filename"] = artifact_path(root, path)
    return result


def _ff(*args, timeout: float = 300):
    return run(["ffmpeg", "-hide_banner", "-nostdin", "-n", *args], timeout=timeout)


def _decode(path: Path, timeout: float = 300) -> None:
    _ff("-v", "error", "-xerror", "-i", path, "-map", "0:v?", "-map", "0:a?", "-f", "null", "-", timeout=timeout)


def _meter(path: Path, integrated: float = -16, peak: float = -1.5, lra: float = 11) -> dict:
    result = _ff("-i", path, "-map", "0:a:0", "-af",
                 f"loudnorm=I={integrated}:TP={peak}:LRA={lra}:print_format=json", "-f", "null", "-")
    matches = re.findall(r'\{\s*"input_i".*?\}', result.stderr, re.S)
    if not matches:
        raise ValueError("FFmpeg did not return a loudness measurement")
    return json.loads(matches[-1])


def _loudness(measured: dict) -> dict:
    def finite(key):
        value = float(measured[key])
        return value if math.isfinite(value) else None
    return {"integrated_lufs": finite("input_i"), "true_peak_dbtp": finite("input_tp"),
            "loudness_range_lu": finite("input_lra"), "threshold_lufs": finite("input_thresh")}


def _save_json(root: Path, value: str, payload: dict) -> str:
    destination = output_path(root, value)
    with tempfile.TemporaryDirectory(prefix=".media-", dir=root) as temporary:
        staged = Path(temporary) / "result.json"
        staged.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        json.loads(staged.read_text(encoding="utf-8"))
        publish(staged, destination)
    return artifact_path(root, destination)


def media_inspect(workspace: Path, request: dict) -> dict:
    """Inspect path using modes probe/frames/loudness/decode/audio_preview.

    Frames use timestamps in seconds and fresh PNGs in output_dir, default
    artifacts/previews. audio_preview is {output,start,duration}, at most 30s.
    All paths are workspace paths. No listening or aesthetic review is implied.
    """
    if not isinstance(request, dict):
        raise ValueError("media_inspect request must be an object")
    root = workspace.resolve()
    path = input_path(root, request.get("path"))
    modes = request.get("modes") or ["probe"]
    allowed = {"probe", "frames", "loudness", "decode", "audio_preview"}
    if not isinstance(modes, list) or any(not isinstance(mode, str) or mode not in allowed for mode in modes):
        raise ValueError(f"modes must be a list drawn from {sorted(allowed)}")
    info = _info(root, path)
    result = {"status": "ok", "path": artifact_path(root, path), "probe": info, "duration": _duration(info)}
    if "loudness" in modes:
        _stream(info, "audio")
        result["loudness"] = _loudness(_meter(path))
    if "decode" in modes:
        _decode(path)
        result["decode"] = {"status": "passed"}
    if "frames" in modes:
        video = _stream(info, "video")
        duration = _duration(info, "video")
        timestamps = request.get("timestamps") or [0.0]
        if not isinstance(timestamps, list) or not 1 <= len(timestamps) <= 24:
            raise ValueError("Request 1 to 24 frame timestamps per call")
        timestamps = [_number(value, "timestamp", 0, duration) for value in timestamps]
        if any(value >= duration for value in timestamps) or len(set(timestamps)) != len(timestamps):
            raise ValueError("Frame timestamps must be distinct and before the video end")
        directory = request.get("output_dir", "artifacts/previews")
        if not isinstance(directory, str):
            raise ValueError("output_dir must be a workspace path")
        stem = re.sub(r"[^A-Za-z0-9_-]", "_", path.stem)[:40]  # Frames of different sources share this directory.
        tag = hashlib.sha256(artifact_path(root, path).encode()).hexdigest()[:8]
        destinations = [output_path(root, f"{directory.rstrip('/')}/{stem}-{tag}-frame-{index:03}-{time:.6f}.png")
                        for index, time in enumerate(timestamps)]
        frames = []
        with tempfile.TemporaryDirectory(prefix=".media-", dir=root) as temporary:
            staged_frames = []
            for index, timestamp in enumerate(timestamps):
                staged = Path(temporary) / f"frame-{index}.png"
                rendered = _ff("-ss", str(timestamp), "-i", path, "-map", "0:v:0", "-an",
                               "-vf", "showinfo", "-frames:v", "1", "-update", "1", staged)
                _decode(staged)
                frame_info = probe(staged)
                selected = re.search(r"\bn:\s*0\b.*?pts_time:([-+\d.eE]+)", rendered.stderr)
                actual = timestamp + float(selected[1]) if selected else None
                if actual is None or not math.isfinite(actual):
                    raise ValueError("Could not measure the selected frame timestamp")
                frames.append({"path": artifact_path(root, destinations[index]), "timestamp": actual,
                               "requested_timestamp": timestamp, "width": frame_info["streams"][0]["width"],
                               "height": frame_info["streams"][0]["height"]})
                staged_frames.append(staged)
            for staged, destination in zip(staged_frames, destinations):
                publish(staged, destination)
        result["frames"] = frames
        result["frame_timebase"] = "seconds from input start, measured from decoded frame PTS"
    if "audio_preview" in modes or request.get("audio_preview") is not None:
        _stream(info, "audio")
        options = request.get("audio_preview") or {}
        if not isinstance(options, dict):
            raise ValueError("audio_preview must be an object")
        start = _number(options.get("start", 0), "preview start", 0, _duration(info, "audio"))
        duration = _number(options.get("duration", min(10, _duration(info, "audio") - start)), "preview duration", 1e-6, 30)
        if start + duration > _duration(info, "audio") + 1e-6:
            raise ValueError("Audio preview exceeds the input duration")
        directory = request.get("output_dir", "artifacts/previews")
        if not isinstance(directory, str):
            raise ValueError("output_dir must be a workspace path")
        destination = output_path(root, options.get("output", directory.rstrip("/") + "/audio-preview.wav"))
        if destination.suffix.lower() != ".wav":
            raise ValueError("Audio previews require a .wav output")
        with tempfile.TemporaryDirectory(prefix=".media-", dir=root) as temporary:
            staged = Path(temporary) / "preview.wav"
            _ff("-ss", str(start), "-i", path, "-t", str(duration), "-map", "0:a:0", "-vn", "-c:a", "pcm_s16le", staged)
            _decode(staged)
            actual_duration = _duration(probe(staged))
            publish(staged, destination)
        result["audio_preview"] = {"path": artifact_path(root, destination), "start": start,
                                   "duration": actual_duration, "probe": _info(root, destination)}
    return result


def audio_timing(workspace: Path, request: dict) -> dict:
    """Measure silence, RMS energy peaks, or positive RMS onsets; never musical beats.

    Required path. method='silence'|'rms'|'onsets'; optional start/end/output.
    Silence options: threshold_db=-35, minimum_silence=.2.
    RMS options: window_ms=50, threshold_ratio=.35, minimum_interval=.25.
    Analyze at most one hour per call. Optional include_envelope returns <=2000 samples.
    """
    if not isinstance(request, dict):
        raise ValueError("audio_timing request must be an object")
    root = workspace.resolve()
    path = input_path(root, request.get("path"))
    info = probe(path)
    duration = _duration(info, "audio")
    start = _number(request.get("start", 0), "start", 0, duration)
    end = _number(request.get("end", duration), "end", 0, duration)
    if not 0 < end - start <= 3600:
        raise ValueError("Analyze a positive range no longer than 3600 seconds")
    method = request.get("method", "silence")
    result = {"status": "ok", "path": artifact_path(root, path), "duration": duration,
              "range": {"start": start, "end": end}, "review_status": "not auditioned"}
    if method == "silence":
        threshold = _number(request.get("threshold_db", -35), "threshold_db", -100, 0)
        minimum = _number(request.get("minimum_silence", min(.2, end-start)), "minimum_silence", .001, end-start)
        logged = _ff("-ss", str(start), "-i", path, "-t", str(end-start), "-map", "0:a:0", "-af",
                     f"silencedetect=noise={threshold}dB:d={minimum}", "-f", "null", "-")
        silences, opened = [], None
        for match in re.finditer(r"silence_(start|end):\s*([-+\d.eE]+)", logged.stderr):
            time = min(end, max(start, start + float(match[2])))
            if match[1] == "start":
                opened = time
            else:
                opened = start if opened is None else opened
                if time > opened:
                    silences.append({"start": opened, "end": time, "duration": time-opened})
                opened = None
        if opened is not None and end > opened:
            silences.append({"start": opened, "end": end, "duration": end-opened})
        result.update(method="silencedetect", settings={"threshold_db": threshold, "minimum_silence": minimum}, silences=silences)
    elif method in {"rms", "onsets"}:
        window_ms = _number(request.get("window_ms", 50), "window_ms", 5, 1000)
        ratio = _number(request.get("threshold_ratio", .35), "threshold_ratio", .01, 1)
        spacing = _number(request.get("minimum_interval", .25), "minimum_interval", 0, 3600)
        rate, energy, times = 16000, [], []
        count = max(1, round(rate * window_ms / 1000))
        with tempfile.TemporaryDirectory(prefix=".media-", dir=root) as temporary:
            decoded = Path(temporary) / "analysis.wav"
            _ff("-ss", str(start), "-i", path, "-t", str(end-start), "-map", "0:a:0", "-ac", "1", "-ar", str(rate), "-c:a", "pcm_s16le", decoded)
            with wave.open(str(decoded), "rb") as audio:
                offset = 0
                while chunk := audio.readframes(count):
                    values = array.array("h", chunk)
                    if sys.byteorder != "little":
                        values.byteswap()
                    rms = math.sqrt(sum(value * value for value in values) / len(values)) / 32768
                    energy.append(rms)
                    times.append(start + (offset + len(values)/2) / rate)
                    offset += len(values)
        scores = energy if method == "rms" else [0.0, *[max(0, right-left) for left, right in zip(energy, energy[1:])]]
        maximum = max(scores, default=0)
        candidates = [index for index in range(1, len(scores)-1)
                      if scores[index] >= maximum * ratio and scores[index] > 0
                      and scores[index] > scores[index-1] and scores[index] >= scores[index+1]]
        selected = []
        for index in candidates:
            if selected and times[index] - times[selected[-1]] < spacing:
                if scores[index] > scores[selected[-1]]:
                    selected[-1] = index
            else:
                selected.append(index)
        result.update(method="rms-peaks" if method == "rms" else "positive-rms-difference",
                      settings={"sample_rate": rate, "window_ms": count*1000/rate, "threshold_ratio": ratio, "minimum_interval": spacing},
                      events=[{"id": f"event-{i+1}", "time": times[index], "strength": scores[index],
                               "kind": "energy_peak" if method == "rms" else "energy_onset"} for i, index in enumerate(selected)],
                      window_count=len(energy), peak_rms=max(energy, default=0),
                      limitation="Energy accents are not detected musical beats or speech boundaries.")
        if request.get("include_envelope"):
            stride = max(1, math.ceil(len(energy)/2000))
            result["envelope"] = [{"time": times[i], "rms": energy[i]} for i in range(0, len(energy), stride)]
            result["envelope_stride_windows"] = stride
    else:
        raise ValueError("method must be silence, rms, or onsets")
    if request.get("output"):
        result["output_path"] = _save_json(root, request["output"], result)
    return result


def _read_midi(data: bytes) -> tuple[int, int, list[dict], list[dict], list[str]]:
    """Read SMF format 0/1 channel events and relevant metadata without dependencies."""
    if len(data) < 14 or data[:4] != b"MThd":
        raise ValueError("Expected a Standard MIDI File header")
    header_size = struct.unpack_from(">I", data, 4)[0]
    if header_size < 6 or 8 + header_size > len(data):
        raise ValueError("Invalid MIDI header length")
    format_, track_count, division = struct.unpack_from(">HHH", data, 8)
    if format_ not in {0, 1} or not 1 <= track_count <= 256 or not division:
        raise ValueError("Support is limited to MIDI format 0/1 with 1..256 tracks and valid timing")
    if format_ == 0 and track_count != 1:
        raise ValueError("MIDI format 0 must have one track")
    tracks, tempos, warnings = [], [], []
    position, event_count = 8 + header_size, 0
    for track_index in range(track_count):
        if position + 8 > len(data) or data[position:position+4] != b"MTrk":
            raise ValueError("Missing or truncated MIDI track")
        size = struct.unpack_from(">I", data, position+4)[0]
        position += 8
        content = data[position:position+size]
        if len(content) != size:
            raise ValueError("Truncated MIDI track data")
        position += size
        cursor, tick, running = 0, 0, None
        active, notes, lyrics = defaultdict(deque), [], []
        name = f"Track {track_index}"

        def take(count: int) -> bytes:
            nonlocal cursor
            if cursor + count > len(content):
                raise ValueError("Truncated MIDI event")
            value = content[cursor:cursor+count]
            cursor += count
            return value

        def variable() -> int:
            value = 0
            for _ in range(4):
                byte = take(1)[0]
                value = (value << 7) | (byte & 127)
                if byte < 128:
                    return value
            raise ValueError("MIDI variable-length number exceeds four bytes")

        while cursor < len(content):
            tick += variable()
            status = take(1)[0]
            event_count += 1
            if event_count > 200000:
                raise ValueError("MIDI exceeds the 200000-event inspection limit")
            if status < 128:
                if running is None:
                    raise ValueError("MIDI running status has no preceding channel status")
                cursor -= 1
                status = running
            elif status < 240:
                running = status
            if status == 255:
                kind = take(1)[0]
                payload = take(variable())
                if kind == 81:
                    if len(payload) != 3 or not int.from_bytes(payload, "big"):
                        raise ValueError("Invalid MIDI tempo event")
                    tempos.append({"tick": tick, "microseconds_per_beat": int.from_bytes(payload, "big"), "track": track_index})
                elif kind == 3:
                    name = payload.decode("utf-8", errors="replace")
                elif kind in {1, 5}:
                    lyrics.append({"tick": tick, "kind": "lyric" if kind == 5 else "text", "text": payload.decode("utf-8", errors="replace")})
                elif kind == 47:
                    break
            elif status in {240, 247}:
                take(variable())
                running = None
            elif 128 <= status < 240:
                kind, channel = status >> 4, status & 15
                values = take(1 if kind in {12, 13} else 2)
                if any(value >= 128 for value in values):
                    raise ValueError("MIDI channel data byte is out of range")
                if kind == 9 and values[1]:
                    key = channel, values[0]
                    if active[key]:
                        warnings.append(f"Track {track_index}: overlapping note-on for channel {channel}, pitch {values[0]}; paired FIFO")
                    active[key].append((tick, values[1]))
                elif kind == 8 or (kind == 9 and values[1] == 0):
                    key = channel, values[0]
                    if active[key]:
                        start, velocity = active[key].popleft()
                        notes.append({"midi": values[0], "channel": channel, "velocity": velocity, "start_tick": start, "end_tick": tick})
                    else:
                        warnings.append(f"Track {track_index}: unmatched note-off at tick {tick}")
            else:
                raise ValueError(f"Unsupported MIDI event status {status:#x}")
        if any(active.values()):
            warnings.append(f"Track {track_index}: unmatched note-on events omitted from completed notes")
        tracks.append({"index": track_index, "name": name, "end_tick": tick, "notes": notes, "text_events": lyrics})
    return format_, division, tracks, tempos, warnings


def score_read(workspace: Path, request: dict) -> dict:
    """Read MIDI track notes, lyrics metadata, rests, and the full tempo map.

    Request path, optional track index, and optional JSON output. Format 0/1
    PPQN and SMPTE clocks are supported. Note lengths end at note-off; sustain
    is not simulated. This does not infer the vocal line or align lyric text.
    """
    if not isinstance(request, dict):
        raise ValueError("score_read request must be an object")
    root = workspace.resolve()
    path = input_path(root, request.get("path"))
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("MIDI inspection is limited to 16 MiB per file")
    format_, division, tracks, changes, warnings = _read_midi(path.read_bytes())
    explicit_tempos = {}
    for event in changes:
        tick, tempo = event["tick"], event["microseconds_per_beat"]
        if tick in explicit_tempos and explicit_tempos[tick] != tempo:
            raise ValueError(f"Conflicting tempo values at tick {tick}")
        explicit_tempos[tick] = tempo
    tempos = {0: 500000, **explicit_tempos}
    ticks = sorted(tempos)
    if division & 0x8000:
        frame_code = (division >> 8) - 256
        ticks_per_frame = division & 255
        if frame_code not in {-24, -25, -29, -30} or not ticks_per_frame:
            raise ValueError("Invalid SMPTE MIDI division")
        rate = Fraction(30000, 1001) if frame_code == -29 else Fraction(-frame_code)
        def seconds(tick):
            return float(Fraction(tick, ticks_per_frame) / rate)
        timing = {"type": "smpte", "frames_per_second": str(rate), "ticks_per_frame": ticks_per_frame}
    else:
        accumulated = [0.0]
        for previous, current in zip(ticks, ticks[1:]):
            accumulated.append(accumulated[-1] + (current-previous) * tempos[previous] / (division * 1e6))
        def seconds(tick):
            index = bisect_right(ticks, tick) - 1
            return accumulated[index] + (tick-ticks[index]) * tempos[ticks[index]] / (division * 1e6)
        timing = {"type": "ticks-per-beat", "ticks_per_beat": division}
    duration = seconds(max(track["end_tick"] for track in tracks))
    if "track" in request and request["track"] is not None:
        selected = _integer(request["track"], "track", 0, len(tracks)-1)
        tracks = [tracks[selected]]
    names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    for track in tracks:
        latest_end, rests = 0.0, []
        track["notes"].sort(key=lambda note: (note["start_tick"], note["channel"], note["midi"]))
        for note in track["notes"]:
            note.update(start=seconds(note["start_tick"]), end=seconds(note["end_tick"]),
                        pitch=names[note["midi"] % 12] + str(note["midi"] // 12 - 1))
            note["duration"] = note["end"] - note["start"]
            if note["duration"] == 0:
                warnings.append(f"Track {track['index']}: zero-length note at tick {note['start_tick']}")
            if note["start"] > latest_end:
                rests.append({"start": latest_end, "end": note["start"]})
            latest_end = max(latest_end, note["end"])
        if duration > latest_end:
            rests.append({"start": latest_end, "end": duration})
        for event in track["text_events"]:
            event["time"] = seconds(event["tick"])
        track["rests"] = rests
    result = {"status": "ok", "path": artifact_path(root, path), "format": format_, "timing": timing,
              "duration": duration, "tempo_changes": [{"tick": tick, "time": seconds(tick), "microseconds_per_beat": tempos[tick], "bpm": 60e6/tempos[tick]} for tick in ticks],
              "tracks": tracks, "warnings": warnings,
              "note_duration_basis": "note-on to note-off; sustain pedal is not applied",
              "alignment_status": "no lyric-to-note alignment or vocal-track selection inferred"}
    if request.get("output"):
        result["output_path"] = _save_json(root, request["output"], result)
    return result


def _audio_codec(path: Path) -> str:
    codecs = {".wav": "pcm_s24le", ".flac": "flac"}
    if path.suffix.lower() not in codecs:
        raise ValueError("Audio renders require a .wav or .flac destination")
    return codecs[path.suffix.lower()]


def _video_destination(path: Path) -> None:
    if path.suffix.lower() not in {".mp4", ".mov", ".mkv"}:
        raise ValueError("Video renders require .mp4, .mov, or .mkv")


def _audio_settings(info: dict, request: dict) -> tuple[int, int]:
    audio = _stream(info, "audio")
    return (_integer(request.get("sample_rate", audio["sample_rate"]), "sample_rate", 8000, 192000),
            _integer(request.get("channels", audio["channels"]), "channels", 1, 8))


def _layout(channels: int) -> str:
    if channels not in {1, 2}:
        raise ValueError("Mixing/concatenation support mono or stereo; set channels to 1 or 2")
    return "mono" if channels == 1 else "stereo"


def _display_size(video: dict) -> tuple[int, int]:
    """Coded size with the display matrix applied, as FFmpeg auto-rotates on decode."""
    rotation = next((side["rotation"] for side in video.get("side_data_list") or [] if "rotation" in side),
                    (video.get("tags") or {}).get("rotate", 0))
    try:
        turned = abs(int(float(rotation))) % 180 == 90
    except (TypeError, ValueError):
        turned = False
    return (video["height"], video["width"]) if turned else (video["width"], video["height"])


def _video_settings(info: dict, request: dict) -> tuple[int, int, Fraction, str]:
    video = _stream(info, "video")
    source_width, source_height = _display_size(video)
    width = _integer(request.get("width", source_width), "width", 2, 7680)
    height = _integer(request.get("height", source_height), "height", 2, 7680)
    if width % 2 or height % 2:
        raise ValueError("Output width and height must be even for yuv420p")
    raw_fps = request.get("fps", video.get("avg_frame_rate") or video.get("r_frame_rate") or "30")
    try:
        fps = Fraction(str(raw_fps))
    except (ValueError, ZeroDivisionError) as error:
        raise ValueError("fps must be a number or rational such as '30000/1001'") from error
    if not 1 <= fps <= 120:
        raise ValueError("fps must be between 1 and 120")
    fit = request.get("fit", "pad")
    if fit not in {"crop", "pad"}:
        raise ValueError("fit must be crop or pad")
    return width, height, fps, fit


def _picture_filter(width: int, height: int, fps: Fraction, fit: str) -> str:
    scaling = "increase" if fit == "crop" else "decrease"
    framing = f"crop={width}:{height}" if fit == "crop" else f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black"
    return (f"fps={fps},scale={width}:{height}:force_original_aspect_ratio={scaling}:force_divisible_by=2,"
            f"{framing},setsar=1,format=yuv420p")


def _timeline(root: Path, request: dict, staged: Path, paths: list[str] | None = None) -> tuple[float, dict]:
    shots = request.get("shots") if paths is None else [{"path": path} for path in paths]
    if not isinstance(shots, list) or not 1 <= len(shots) <= 64 or any(not isinstance(shot, dict) for shot in shots):
        raise ValueError("A timeline needs 1..64 shot objects")
    sources = [input_path(root, shot.get("path", shot.get("source_path"))) for shot in shots]
    infos = [probe(source) for source in sources]
    width, height, fps, fit = _video_settings(infos[0], request)
    policy = request.get("audio_policy", "replace" if request.get("audio") else "keep")
    if policy not in {"keep", "mute", "replace"}:
        raise ValueError("audio_policy must be keep, mute, or replace")
    if policy == "replace" and not request.get("audio"):
        raise ValueError("audio_policy=replace requires audio")
    rate = _integer(request.get("sample_rate", 48000), "sample_rate", 8000, 192000)
    channels = _integer(request.get("channels", 2), "channels", 1, 2)
    filters, rows, args, cursor, ids = [], [], [], 0.0, set()
    frame_end = 0
    for index, (shot, source, info) in enumerate(zip(shots, sources, infos)):
        source_duration = _duration(info, "video")
        source_start = _number(shot.get("source_start", 0), "source_start", 0, source_duration)
        source_end = _number(shot.get("source_end", source_duration), "source_end", 0, source_duration)
        start = _number(shot.get("start", cursor), "shot start")
        end = _number(shot.get("end", start + source_end-source_start), "shot end")
        if source_end <= source_start or end <= start or abs(start-cursor) > 1e-6:
            raise ValueError("Shots require positive source/target spans and a continuous timeline starting at zero")
        if _number(shot.get("speed", 1), "speed", .001, 100) != 1 or abs((end-start)-(source_end-source_start)) > 1e-6:
            raise ValueError("This renderer supports speed=1 hard cuts; source and target lengths must match")
        scene_id = str(shot.get("id", f"shot-{index+1}"))
        if scene_id in ids:
            raise ValueError(f"Duplicate shot id: {scene_id}")
        ids.add(scene_id)
        next_frame = round(end * fps)
        frames = next_frame-frame_end
        if frames < 1:
            raise ValueError("A shot becomes shorter than one output frame")
        actual_duration = float(Fraction(frames, 1)/fps)
        args += ["-i", source]
        filters.append(f"[{index}:v:0]trim=start={source_start}:end={source_end},setpts=PTS-STARTPTS,"
                       f"{_picture_filter(width, height, fps, fit)},tpad=stop_mode=clone:stop=-1,"  # Source trims can fall one frame short of the plan.
                       f"trim=end_frame={frames},setpts=N/(({fps})*TB)[v{index}]")
        source_audio = shot.get("source_audio", "keep" if policy == "keep" else "mute")
        if source_audio not in {"keep", "mute"}:
            raise ValueError("shot source_audio must be keep or mute")
        if policy == "keep":
            if _stream(info, "audio", required=False) and source_audio == "keep":
                filters.append(f"[{index}:a:0]atrim=start={source_start}:end={source_end},asetpts=PTS-STARTPTS,"
                               f"aresample={rate},aformat=sample_fmts=fltp:channel_layouts={_layout(channels)},"
                               f"apad,atrim=duration={actual_duration}[a{index}]")
            else:
                filters.append(f"anullsrc=r={rate}:cl={_layout(channels)},atrim=duration={actual_duration}[a{index}]")
        rows.append({"id": scene_id, "path": artifact_path(root, source), "start": start, "end": end,
                     "source_start": source_start, "source_end": source_end, "source_audio": source_audio,
                     "output_start_frame": frame_end, "output_end_frame": next_frame})
        cursor, frame_end = end, next_frame
    if "duration" in request and abs(_number(request["duration"], "duration")-cursor) > 1e-6:
        raise ValueError("Requested duration differs from the complete shot timeline")
    duration = float(Fraction(frame_end, 1)/fps)
    inputs = "".join(f"[v{index}][a{index}]" if policy == "keep" else f"[v{index}]" for index in range(len(shots)))
    filters.append(f"{inputs}concat=n={len(shots)}:v=1:a={int(policy == 'keep')}[video]" + ("[audio]" if policy == "keep" else ""))
    if policy == "replace":
        soundtrack = input_path(root, request["audio"])
        _stream(probe(soundtrack), "audio")
        args += ["-i", soundtrack]
        filters.append(f"[{len(shots)}:a:0]aresample={rate},apad,atrim=duration={duration},asetpts=PTS-STARTPTS[audio]")
    args += ["-filter_complex", ";".join(filters), "-map", "[video]"]
    if policy != "mute":
        args += ["-map", "[audio]", "-c:a", "aac", "-ar", str(rate), "-ac", str(channels), "-b:a", "192k"]
    args += ["-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p", "-r", str(fps), "-frames:v", str(frame_end), staged]
    _ff(*args, timeout=_number(request.get("timeout", 300), "timeout", 1, 3600))
    return duration, {"shots": rows, "audio_policy": policy, "width": width, "height": height,
                      "fps": str(fps), "fit": fit, "expected_video_frames": frame_end,
                      "planned_duration": cursor, "duration_rounding": "absolute boundaries rounded to output frames"}


def media_render(workspace: Path, request: dict) -> dict:
    """Render to a new output with op extract_audio/resample/normalize/trim/concat/mix/mux/timeline.

    Single input: path. concat: paths. mix: path+music, optional duration, music_gain=.16,
    foreground_gain=1, loop_music=True, offsets/fades in seconds. mux: path+audio,
    optional audio_offset, preserving the complete picture. normalize: integrated_lufs=-16,
    true_peak_dbtp=-1.5, loudness_range=11. trim: start/end. resample: sample_rate/channels.
    Timeline shots: {id,path,source_start,source_end,start,end,source_audio:'keep'|'mute'};
    hard cuts at speed 1, width/height/fps, fit='pad'|'crop', audio_policy keep/mute/replace.
    Audio outputs WAV/FLAC; video MP4/MOV/MKV. Every output is probed and decoded before publication.
    """
    if not isinstance(request, dict):
        raise ValueError("media_render request must be an object")
    root = workspace.resolve()
    operation = request.get("op")
    allowed = {"extract_audio", "resample", "normalize", "trim", "concat", "mix", "mux", "timeline"}
    if operation not in allowed:
        raise ValueError(f"op must be one of {sorted(allowed)}")
    if not isinstance(request.get("output"), str):
        raise ValueError("output is required")
    destination = output_path(root, request["output"])
    timeout = _number(request.get("timeout", 300), "timeout", 1, 3600)
    path = None if operation in {"concat", "timeline"} else input_path(root, request.get("path"))
    info = probe(path) if path is not None else {}
    details, expected, expected_video, expected_audio = {}, None, False, True
    with tempfile.TemporaryDirectory(prefix=".media-", dir=root) as temporary:
        staged = Path(temporary) / ("render" + destination.suffix.lower())
        if operation == "timeline":
            _video_destination(destination)
            expected, details = _timeline(root, request, staged)
            expected_video, expected_audio = True, details["audio_policy"] != "mute"
        elif operation == "concat":
            values = request.get("paths")
            if not isinstance(values, list) or not 1 <= len(values) <= 64:
                raise ValueError("concat requires 1..64 paths")
            paths = [input_path(root, value) for value in values]
            infos = [probe(source) for source in paths]
            has_video = [bool(_stream(item, "video", required=False)) for item in infos]
            if any(has_video):
                if not all(has_video):
                    raise ValueError("Cannot concatenate audio-only and video inputs")
                _video_destination(destination)
                expected, details = _timeline(root, request, staged, values)
                expected_video, expected_audio = True, details["audio_policy"] != "mute"
            else:
                codec = _audio_codec(destination)
                rate, channels = _audio_settings(infos[0], request)
                filters, args = [], []
                for index, (source, metadata) in enumerate(zip(paths, infos)):
                    _stream(metadata, "audio")
                    args += ["-i", source]
                    filters.append(f"[{index}:a:0]aresample={rate},aformat=sample_fmts=fltp:channel_layouts={_layout(channels)},asetpts=PTS-STARTPTS[a{index}]")
                filters.append("".join(f"[a{index}]" for index in range(len(paths))) + f"concat=n={len(paths)}:v=0:a=1[audio]")
                _ff(*args, "-filter_complex", ";".join(filters), "-map", "[audio]", "-ar", str(rate), "-ac", str(channels), "-c:a", codec, staged, timeout=timeout)
                expected = sum(_duration(item, "audio") for item in infos)
                details = {"paths": [artifact_path(root, source) for source in paths]}
        elif operation in {"extract_audio", "resample"}:
            codec = _audio_codec(destination)
            audio = _stream(info, "audio")
            stream_index = _integer(request.get("stream_index", audio["index"]), "stream_index", 0, 1024)
            audio = next((stream for stream in info["streams"] if stream["index"] == stream_index and stream["codec_type"] == "audio"), None)
            if audio is None:
                raise ValueError("stream_index must identify an audio stream")
            settings_info = {**info, "streams": [audio]}
            rate, channels = _audio_settings(settings_info, request)
            _ff("-i", path, "-map", f"0:{stream_index}", "-vn", "-ar", str(rate), "-ac", str(channels), "-c:a", codec, staged, timeout=timeout)
            expected = _duration(settings_info, "audio")
            details = {"stream_index": stream_index, "sample_rate": rate, "channels": channels,
                       "source_start_offset": float(audio.get("start_time", 0)) - float(_stream(info, "video", required=False).get("start_time", info.get("format", {}).get("start_time", 0)))}
        elif operation == "normalize":
            codec = _audio_codec(destination)
            rate, channels = _audio_settings(info, request)
            integrated = _number(request.get("integrated_lufs", -16), "integrated_lufs", -70, -5)
            peak = _number(request.get("true_peak_dbtp", -1.5), "true_peak_dbtp", -9, 0)
            lra = _number(request.get("loudness_range", 11), "loudness_range", 1, 50)
            before = _meter(path, integrated, peak, lra)
            if any(not math.isfinite(float(before[key])) for key in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")):
                raise ValueError("Silent or unmeasurable audio cannot be loudness-normalized")
            filter_ = (f"loudnorm=I={integrated}:TP={peak}:LRA={lra}:linear=true:print_format=json:"
                       f"measured_I={before['input_i']}:measured_TP={before['input_tp']}:measured_LRA={before['input_lra']}:"
                       f"measured_thresh={before['input_thresh']}:offset={before['target_offset']}")
            rendered = _ff("-i", path, "-map", "0:a:0", "-af", filter_, "-ar", str(rate), "-ac", str(channels), "-c:a", codec, staged, timeout=timeout)
            after = _loudness(_meter(staged, integrated, peak, lra))
            tolerance = _number(request.get("tolerance_lu", .5), "tolerance_lu", .05, 2)
            if after["integrated_lufs"] is None or abs(after["integrated_lufs"]-integrated) > tolerance or after["true_peak_dbtp"] > peak:
                raise ValueError(f"Rendered loudness misses target: {after}")
            processing = re.findall(r'\{\s*"input_i".*?\}', rendered.stderr, re.S)
            details = {"before": _loudness(before), "loudness": after, "targets": {"integrated_lufs": integrated, "true_peak_dbtp": peak, "tolerance_lu": tolerance},
                       "normalization_type": json.loads(processing[-1])["normalization_type"] if processing else "unreported"}
            expected = _duration(info, "audio")
        elif operation == "trim":
            expected_video = bool(_stream(info, "video", required=False))
            source_duration = _duration(info, "video" if expected_video else "audio")
            start = _number(request.get("start", 0), "start", 0, source_duration)
            end = _number(request.get("end", source_duration), "end", 0, source_duration)
            if end <= start:
                raise ValueError("trim end must follow start")
            expected = end-start
            args = ["-i", path, "-ss", str(start), "-t", str(expected)]
            expected_audio = bool(_stream(info, "audio", required=False))
            if expected_video:
                _video_destination(destination)
                args += ["-map", "0:v:0", "-map", "0:a:0?", "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac"]
            else:
                args += ["-map", "0:a:0", "-c:a", _audio_codec(destination)]
            _ff(*args, staged, timeout=timeout)
            details = {"start": start, "end": end}
        elif operation == "mix":
            codec = _audio_codec(destination)
            music = input_path(root, request.get("music"))
            music_info = probe(music)
            _duration(music_info, "audio")
            rate, channels = _audio_settings(info, request)
            foreground_offset = _number(request.get("foreground_offset", 0), "foreground_offset")
            music_offset = _number(request.get("music_offset", 0), "music_offset")
            expected = _number(request.get("duration", foreground_offset + _duration(info, "audio")), "duration", 1e-6, 86400)
            if foreground_offset >= expected or music_offset >= expected:
                raise ValueError("Track offsets must precede the mix end")
            music_gain = _number(request.get("music_gain", .16), "music_gain", 0, 16)
            foreground_gain = _number(request.get("foreground_gain", 1), "foreground_gain", 0, 16)
            fade_in = _number(request.get("fade_in", min(.05, (expected-music_offset)/2)), "fade_in", 0, expected-music_offset)
            fade_out = _number(request.get("fade_out", min(.2, (expected-music_offset)/2)), "fade_out", 0, expected-music_offset)
            args = ["-i", path] + (["-stream_loop", "-1"] if request.get("loop_music", True) else []) + ["-i", music]
            filters = [f"[0:a:0]volume={foreground_gain},adelay={round(foreground_offset*1000)}:all=1,apad,atrim=duration={expected},aresample={rate},aformat=channel_layouts={_layout(channels)}[foreground]",
                       f"[1:a:0]volume={music_gain},adelay={round(music_offset*1000)}:all=1,apad,atrim=duration={expected},aresample={rate},aformat=channel_layouts={_layout(channels)}" +
                       (f",afade=t=in:st={music_offset}:d={fade_in}" if fade_in else "") +
                       (f",afade=t=out:st={expected-fade_out}:d={fade_out}" if fade_out else "") + "[bed]",
                       "[foreground][bed]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[mix]"]
            floating = Path(temporary) / "floating.wav"
            _ff(*args, "-filter_complex", ";".join(filters), "-map", "[mix]", "-c:a", "pcm_f32le", floating, timeout=timeout)
            measured = _loudness(_meter(floating))
            if measured["true_peak_dbtp"] is not None and measured["true_peak_dbtp"] >= 0:
                raise ValueError("Mix would clip; lower foreground_gain/music_gain or normalize the sources")
            _ff("-i", floating, "-c:a", codec, staged, timeout=timeout)
            details = {"music": artifact_path(root, music), "music_gain": music_gain, "foreground_gain": foreground_gain,
                       "foreground_offset": foreground_offset, "music_offset": music_offset, "loop_music": bool(request.get("loop_music", True)),
                       "fade_in": fade_in, "fade_out": fade_out, "loudness": _loudness(_meter(staged))}
        elif operation == "mux":
            _video_destination(destination)
            expected_video = True
            expected = _duration(info, "video")
            audio = input_path(root, request.get("audio"))
            _stream(probe(audio), "audio")
            offset = _number(request.get("audio_offset", 0), "audio_offset", -86400, expected)
            filter_ = f"atrim=start={-offset},asetpts=PTS-STARTPTS," if offset < 0 else f"adelay={round(offset*1000)}:all=1,"
            filter_ += f"apad,atrim=duration={expected}"
            _ff("-i", path, "-i", audio, "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-af", filter_, "-c:a", "aac", "-b:a", "192k", "-t", str(expected), staged, timeout=timeout)
            details = {"audio": artifact_path(root, audio), "audio_offset": offset,
                       "duration_policy": "preserve complete picture; pad or trim replacement audio",
                       "stream_mapping": "first video + replacement audio; other tracks omitted"}
        rendered_info = probe(staged)
        if expected_video:
            _stream(rendered_info, "video")
        if expected_audio:
            _stream(rendered_info, "audio")
        actual = _duration(rendered_info, "video" if expected_video else "audio")
        video = _stream(rendered_info, "video", required=False)
        fps = Fraction(video.get("avg_frame_rate", "0/1")) if video else Fraction(0)
        tolerance = (float(1/fps) if fps else .05) + .025
        if expected is not None and abs(actual-expected) > tolerance:
            raise ValueError(f"Rendered duration {actual:.6f}s differs from expected {expected:.6f}s")
        if "expected_video_frames" in details and video.get("nb_frames") and int(video["nb_frames"]) != details["expected_video_frames"]:
            raise ValueError("Rendered timeline is missing planned video frames")
        _decode(staged, timeout)
        publish(staged, destination)
    return {"status": "ok", "op": operation, "output_path": artifact_path(root, destination),
            "duration": actual, "expected_duration": expected, "probe": _info(root, destination),
            "validation": "probed and fully decoded; perceptual quality not reviewed", **details}
