#!/usr/bin/env python3
"""Build remote and/or local Cursor skill packages.

Profiles
--------
- **remote** (default): ``3dsmax-mcp-remote`` — docs + HTTP helper scripts for agent host A.
  References are copied from ``skills/3dsmax-mcp-dev/*.md`` (shared source).
- **local**: ``3dsmax-mcp-dev`` — maintainer docs; ``scripts/dialog_monitor/README.md`` only
  (no maxmcp Python).
- **both**: build both packages.

Outputs: ``dist/<name>/`` and ``<name>.skill`` zip; optional install into Cursor/Claude dirs.
"""

from __future__ import annotations

import argparse
import re
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEV_SKILL_DIR = ROOT / "skills" / "3dsmax-mcp-dev"
REMOTE_SKILL_DIR = ROOT / "skills" / "3dsmax-mcp-remote"

PROFILES = {
    "remote": {
        "name": "3dsmax-mcp-remote",
        "skill_src": REMOTE_SKILL_DIR / "SKILL.md",
        "scripts_src": REMOTE_SKILL_DIR / "scripts",
        "copy_dev_refs": True,
        "local_scripts_readme_only": False,
    },
    "local": {
        "name": "3dsmax-mcp-dev",
        "skill_src": DEV_SKILL_DIR / "SKILL.md",
        "scripts_src": DEV_SKILL_DIR / "scripts",
        "copy_dev_refs": True,
        "local_scripts_readme_only": True,
    },
}


def collect_dev_reference_mds() -> list[Path]:
    files: list[Path] = []
    for path in sorted(DEV_SKILL_DIR.glob("*.md")):
        if path.name.lower() == "skill.md":
            continue
        files.append(path)
    return files


def rewrite_skill_md(text: str, ref_names: set[str]) -> str:
    for name in sorted(ref_names, key=len, reverse=True):
        text = text.replace(f"]({name})", f"](references/{name})")
        text = re.sub(
            rf"(?<!references/)(`)({re.escape(name)})(`)",
            rf"\1references/\2\3",
            text,
        )
    return text


def _clear_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def _install_dirs(skill_name: str) -> dict[str, list[tuple[str, Path]]]:
    return {
        "local": [
            (".cursor/skills", ROOT / ".cursor" / "skills" / skill_name),
            (".agents/skills", ROOT / ".agents" / "skills" / skill_name),
        ],
        "global": [
            ("~/.cursor/skills", Path.home() / ".cursor" / "skills" / skill_name),
            ("~/.claude/skills", Path.home() / ".claude" / "skills" / skill_name),
            ("~/.agents/skills", Path.home() / ".agents" / "skills" / skill_name),
        ],
    }


def stage_package(profile: str) -> Path:
    cfg = PROFILES[profile]
    skill_name = cfg["name"]
    skill_src: Path = cfg["skill_src"]
    dist_dir = ROOT / "dist" / skill_name

    if not skill_src.is_file():
        print(f"ERROR: source not found: {skill_src}")
        raise SystemExit(1)

    _clear_dir(dist_dir)
    refs_dir = dist_dir / "references"
    refs_dir.mkdir(parents=True, exist_ok=True)

    ref_files = collect_dev_reference_mds() if cfg["copy_dev_refs"] else []
    ref_names = {p.name for p in ref_files}
    for src in ref_files:
        shutil.copy2(src, refs_dir / src.name)
    print(f"  [{skill_name}] references/: {len(ref_files)} markdown files from dev")

    # Remote profile: overlay skills/3dsmax-mcp-remote/references/*.md (agent-facing cuts).
    if profile == "remote":
        remote_refs = REMOTE_SKILL_DIR / "references"
        if remote_refs.is_dir():
            n_ov = 0
            for src in sorted(remote_refs.glob("*.md")):
                shutil.copy2(src, refs_dir / src.name)
                ref_names.add(src.name)
                n_ov += 1
            if n_ov:
                print(f"  [{skill_name}] references/: overlaid {n_ov} remote-specific md")

    scripts_src: Path = cfg["scripts_src"]
    scripts_dst = dist_dir / "scripts"
    if cfg["local_scripts_readme_only"]:
        scripts_dst.mkdir(parents=True, exist_ok=True)
        readme = (
            scripts_src / "dialog_monitor" / "README.md"
            if (scripts_src / "dialog_monitor" / "README.md").is_file()
            else None
        )
        dm = scripts_dst / "dialog_monitor"
        dm.mkdir(parents=True, exist_ok=True)
        if readme and readme.is_file():
            shutil.copy2(readme, dm / "README.md")
        else:
            (dm / "README.md").write_text(
                "Maintainer probes: repo dialog_monitor/ (needs maxmcp).\n"
                "Remote agents: install 3dsmax-mcp-remote.\n",
                encoding="utf-8",
                newline="\n",
            )
        print(f"  [{skill_name}] scripts/dialog_monitor/README.md only")
    else:
        if not scripts_src.is_dir():
            print(f"ERROR: scripts source missing: {scripts_src}")
            raise SystemExit(1)
        if scripts_dst.exists():
            shutil.rmtree(scripts_dst)
        shutil.copytree(
            scripts_src,
            scripts_dst,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git"),
        )
        n_py = sum(1 for _ in scripts_dst.rglob("*.py"))
        print(f"  [{skill_name}] scripts/: copied ({n_py} python files)")

    skill_text = skill_src.read_text(encoding="utf-8")
    skill_text = rewrite_skill_md(skill_text, ref_names)
    (dist_dir / "SKILL.md").write_text(skill_text, encoding="utf-8", newline="\n")
    print(f"  [{skill_name}] SKILL.md")

    return dist_dir


def write_skill_zip(package_dir: Path) -> Path:
    skill_out = ROOT / f"{package_dir.name}.skill"
    with zipfile.ZipFile(skill_out, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(package_dir.rglob("*")):
            if path.is_dir():
                continue
            zf.write(path, path.relative_to(package_dir).as_posix())
    count = sum(1 for p in package_dir.rglob("*") if p.is_file())
    print(f"  Built {skill_out.name} ({count} files)")
    return skill_out


def install_package(package_dir: Path, target: str) -> None:
    skill_name = package_dir.name
    dirs = _install_dirs(skill_name)
    if target == "local":
        dests = dirs["local"]
    elif target == "global":
        dests = dirs["global"]
    else:
        dests = dirs["local"] + dirs["global"]

    for label, dest in dests:
        if dest.is_symlink() or dest.is_junction():
            print(f"  Replacing old symlink: {dest}")
            dest.unlink()
        try:
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(package_dir, dest)
            print(f"  Installed to {label}/{skill_name}/")
        except PermissionError:
            print(f"  WARN: {label} locked, skipped")


def build_one(profile: str, target: str) -> tuple[Path, Path]:
    print(f"Staging {profile} skill package...")
    package_dir = stage_package(profile)
    zip_path = write_skill_zip(package_dir)
    if target != "none":
        print(f"Installing {profile}...")
        install_package(package_dir, target=target)
    print(f"Done [{profile}]. Package: {package_dir}")
    print(f"             Zip:     {zip_path}")
    return package_dir, zip_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build 3dsmax-mcp-remote and/or 3dsmax-mcp-dev skill packages"
    )
    parser.add_argument(
        "--profile",
        choices=["remote", "local", "both"],
        default="remote",
        help="Which skill(s) to build (default: remote)",
    )
    parser.add_argument(
        "--target",
        choices=["local", "global", "both", "none"],
        default="both",
        help="Where to install: local, global, both (default), or none (dist+zip only)",
    )
    args = parser.parse_args()
    profiles = ["remote", "local"] if args.profile == "both" else [args.profile]
    for profile in profiles:
        build_one(profile, args.target)
    if "remote" in profiles:
        print("Hint: copy dist/3dsmax-mcp-remote/ (or .skill) to agent host A.")


if __name__ == "__main__":
    main()
