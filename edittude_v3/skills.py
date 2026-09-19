from __future__ import annotations

from pathlib import Path

from edittude_v3.paths import install_root, unique_existing_dirs


def skills_dir(workspace: Path) -> Path:
    return workspace / "skills"


def skill_dirs(workspace: Path) -> list[Path]:
    """Install skills first, workspace skills last so local folders win."""
    return unique_existing_dirs(install_root() / "skills", workspace / "skills")


def skill_folders(workspace: Path) -> list[Path]:
    found: dict[str, Path] = {}
    for root in skill_dirs(workspace):
        for path in sorted(root.iterdir()):
            if path.is_dir() and (path / "SKILL.md").is_file():
                found[skill_name(path / "SKILL.md")] = path
    return sorted(found.values(), key=lambda path: skill_name(path / "SKILL.md"))


def _frontmatter(skill_md: Path) -> dict[str, str]:
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    data: dict[str, str] = {}
    for line in text[3:end].splitlines():
        key, _, value = line.partition(":")
        if key.strip() and value.strip():
            data[key.strip()] = value.strip()
    return data


def skill_name(skill_md: Path) -> str:
    return _frontmatter(skill_md).get("name") or skill_md.parent.name


def skill_description(skill_md: Path) -> str:
    return _frontmatter(skill_md).get("description") or ""


def list_skill_names(workspace: Path) -> list[str]:
    return [skill_name(path / "SKILL.md") for path in skill_folders(workspace)]


def list_skills(workspace: Path) -> list[tuple[str, str]]:
    return [
        (skill_name(path / "SKILL.md"), skill_description(path / "SKILL.md"))
        for path in skill_folders(workspace)
    ]
