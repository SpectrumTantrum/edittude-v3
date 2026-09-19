from __future__ import annotations

from deepagents.middleware.subagents import SubAgent

INVENTORY: SubAgent = {
    "name": "inventory",
    "description": (
        "Survey a footage folder. ls the path, run inventory, extract thumbs. "
        "Return clip list with durations, codecs, and audio. Use before any cut."
    ),
    "system_prompt": (
        "You inventory footage. List the folder the user named. "
        "Run `edittude-v3 media inventory FOLDER --out PATH`. "
        "Write JSON next to the work, not into the git tree. "
        "If you need eyes, extract thumbs. Report counts, total duration, "
        "and which file looks like voiceover. Do not edit."
    ),
    "skills": ["./skills/"],
}

EDITOR: SubAgent = {
    "name": "editor",
    "description": (
        "Plan a cut and assemble it. Writes an EDL, renders picture with ffmpeg. "
        "Use after inventory when the job is shot selection, pacing, or a recut."
    ),
    "system_prompt": (
        "You are the cutter. Read footage-inventory and editorial-taste, then assembly. "
        "Write an EDL. Prefer `edittude-v3 media plan` then edit the JSON "
        "if the first cut is wrong. Assemble with the media CLI. "
        "Choose a cut. Do not ask the parent for an EDL."
    ),
    "skills": ["./skills/"],
}

MIXER: SubAgent = {
    "name": "mixer",
    "description": (
        "Voiceover, music, ducking, loudnorm. Use when picture exists and audio needs a mix."
    ),
    "system_prompt": (
        "You mix. Read the mix skill. Duck iPhone ambient under voiceover. "
        "Do not mute the world unless the brief says so. Target about -16 LUFS. "
        "Use `edittude-v3 media mix` or finish."
    ),
    "skills": ["./skills/"],
}

QC: SubAgent = {
    "name": "qc",
    "description": (
        "Review a render: duration, black frames, silence, loudness, freezes, frame grabs. "
        "Return pass/fail and what to recut."
    ),
    "system_prompt": (
        "You QC. Run `edittude-v3 media qc VIDEO --out qc.json` "
        "and grab frames. Report issues in plain language. Suggest a recut if it fails."
    ),
    "skills": ["./skills/"],
}


def video_subagents() -> list[SubAgent]:
    return [INVENTORY, EDITOR, MIXER, QC]
