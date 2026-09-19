# edittude-v3

Local video-editing agent. Model is DeepSeek Flash.

It inventories a footage folder, plans a cut, renders with ffmpeg, mixes voiceover, grades, titles, then QCs. You do not have to supply an EDL.

## Install

```bash
curl -LsSf https://raw.githubusercontent.com/SpectrumTantrum/edittude-v3/main/install.sh | bash
```

That installs `uv` if needed, syncs the project, and puts `edittude-v3` in `~/.local/bin`. From a checkout you can run `./install.sh` instead.

ffmpeg and ffprobe must be on PATH. On a Mac: `brew install ffmpeg`.

Put a DeepSeek key in the `.env` the installer printed, or in the folder you run from:

```
DEEPSEEK_API_KEY=sk-...
```

Get a key from https://platform.deepseek.com

## CLI

```bash
edittude-v3
edittude-v3 ask "make a cut from /path/to/footage"
edittude-v3 skills
edittude-v3 media --help
edittude-v3 -C /path/to/project
```

`edittude-v3` with no args opens a session in the current directory. Transcript stays in normal terminal scrollback. The composer sits at the bottom.

From a checkout without the installer:

```bash
uv sync
uv run edittude-v3
```

## Media tools

These are what the agent should run instead of inventing ffmpeg filters:

```bash
edittude-v3 media inventory FOLDER --out inventory.json
edittude-v3 media plan inventory.json --out edl.json --title "A DAY OUT"
edittude-v3 media assemble edl.json --out picture.mp4
edittude-v3 media finish picture.mp4 edl.json --out final.mp4
edittude-v3 media qc final.mp4 --out qc.json
edittude-v3 media proof FOLDER --out /path/to/artifacts
```

Write large renders next to the footage, not into this repo.

## Keys

```
enter                 send
alt+enter / ctrl+j    newline
/                     slash commands
@                     mention a file
esc                   interrupt the current turn
ctrl+c                interrupt
ctrl+d                quit
↑ / ↓                 history
```

## Slash commands

```
/help     commands and keys
/new      fresh thread
/skills   list skill folders
/status   model, thread, workspace
/clear    clear the transcript
/quit     exit
```

## Skills

Each skill is a folder with a `SKILL.md`:

```
skills/
  footage-inventory/
    SKILL.md
```

The agent loads the install `skills/` plus `./skills/` in the current directory. Drop more folders in.

## Credit

Thanks to [Deep Agents](https://github.com/langchain-ai/deepagents) for the harness, and to [HKU Data Science](https://github.com/HKUDS) for [VideoAgent](https://github.com/HKUDS/VideoAgent), the voice agents project that inspired this one.
