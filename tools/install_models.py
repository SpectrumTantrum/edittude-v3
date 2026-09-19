"""Install the optional neural model runtime and its pinned weights. Needs network access.

This is the only part of tools/ that downloads anything. Inference itself stays offline:
everything lands in models_root() and a separate .venv-models interpreter, so the agent
virtualenv never gains torch.
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.common import install_root, model_python, models_root
from tools.models import capabilities

# Shared with every backend; the pins come from edittude-v2's asr extra. TorchAudio 2.9
# removed APIs these backends still call.
SHARED = ["torch>=2.6,<2.9", "torchaudio>=2.6,<2.9", "numpy<3", "soundfile", "huggingface-hub>=0.34,<1"]

_DEMUCS = "https://dl.fbaipublicfiles.com/demucs/hybrid_transformer/"
_REMOTE = "https://raw.githubusercontent.com/facebookresearch/demucs/e976d93ecc3865e5757426930257e200846a520a/demucs/remote/"

# Phase B adds "voice-conversion" and "singing" entries here plus a clone/patch helper.
# files entries are (destination under models_root(), url, sha256 or its demucs prefix).
BACKENDS = {
    "asr": {
        "tool": "speech_transcribe", "deps": ["faster-whisper>=1.2,<2"], "size_gb": 0.15,
        "hf": ("Systran/faster-whisper-base", "ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66",
               "asr/faster-whisper-base"),
        "files": [],
    },
    "separation": {
        "tool": "audio_separate", "deps": ["demucs>=4.1,<5"], "size_gb": 0.1, "hf": None,
        # htdemucs.yaml is a bag of exactly one signature: 955717e8.
        "files": [
            ("demucs/htdemucs.yaml", _REMOTE + "htdemucs.yaml",
             "239c445d0b14454d541ad8bd9bb271c9e536d267e8a4625208744cbb2e7bb66c"),
            # ponytail: upstream publishes no full digest for the .th, so this verifies the
            # 8-hex sha256 prefix demucs itself checks. Swap in a full digest if one appears.
            ("demucs/955717e8-8726e21a.th", _DEMUCS + "955717e8-8726e21a.th", "8726e21a"),
        ],
    },
}


def _uv() -> str:
    found = shutil.which("uv") or str(Path.home() / ".local/bin/uv")
    if not Path(found).is_file():
        raise SystemExit("uv is missing. Re-run the edittude-v3 installer.")
    return found


def _run(args: list[str | Path]) -> None:
    subprocess.run([str(arg) for arg in args], check=True)


def _venv(deps: list[str]) -> None:
    python = model_python()
    if not python.is_file():
        print(f"creating {python.parent.parent}")
        _run([_uv(), "venv", "--python", "3.11", python.parent.parent])
    _run([_uv(), "pip", "install", "--quiet", "--python", python, *deps])


def _download(destination: Path, url: str, digest: str) -> None:
    if destination.is_file() and hashlib.sha256(destination.read_bytes()).hexdigest().startswith(digest):
        print(f"have   {destination.name}")
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    staged = destination.with_suffix(destination.suffix + ".part")
    print(f"fetch  {destination.name}")
    urllib.request.urlretrieve(url, staged)  # noqa: S310 - pinned https URLs above.
    if not hashlib.sha256(staged.read_bytes()).hexdigest().startswith(digest):
        staged.unlink()
        raise SystemExit(f"Checksum mismatch for {url}")
    staged.replace(destination)


def _snapshot(repo_id: str, revision: str, destination: Path) -> None:
    """Download pinned HF weights from inside the model venv, not the agent venv."""
    print(f"fetch  {repo_id}@{revision[:8]}")
    _run([model_python(), "-c", "import sys; from huggingface_hub import snapshot_download;"
          " snapshot_download(sys.argv[1], revision=sys.argv[2], local_dir=sys.argv[3])",
          repo_id, revision, destination])


def install(names: list[str]) -> None:
    total = sum(BACKENDS[name]["size_gb"] for name in names)
    print(f"Installing {', '.join(names)}: about {total:.2f} GB of weights "
          f"plus about 2.5 GB of torch runtime into {install_root()}")
    _venv(SHARED + [dep for name in names for dep in BACKENDS[name]["deps"]])
    for name in names:
        backend = BACKENDS[name]
        for relative, url, digest in backend["files"]:
            _download(models_root() / relative, url, digest)
        if backend["hf"]:
            repo_id, revision, relative = backend["hf"]
            _snapshot(repo_id, revision, models_root() / relative)


def check(names: list[str]) -> int:
    reported = capabilities()
    failed = 0
    for name in names:
        status = reported[BACKENDS[name]["tool"]]
        print(f"{name:12} {status['status']:12} {status['reason']}")
        failed += status["status"] != "configured"
    return int(bool(failed))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="edittude-media models install", description=__doc__)
    parser.add_argument("--models", default="asr,separation",
                        help="comma-separated backends (default: asr,separation)")
    parser.add_argument("--check", action="store_true",
                        help="report backend status without downloading anything")
    args = parser.parse_args(argv)
    names = [name for name in args.models.split(",") if name]
    unknown = [name for name in names if name not in BACKENDS]
    if unknown:
        parser.error(f"Unknown model backend: {', '.join(unknown)}. Valid: {', '.join(BACKENDS)}")
    if args.check:
        return check(names)
    install(names)
    return check(names)


if __name__ == "__main__":
    raise SystemExit(main())
