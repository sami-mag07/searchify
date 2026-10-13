#!/usr/bin/env python3
"""Messdaten-Paket vom Mac (train/make_bundle.sh) auf dem Laptop auspacken.

  python train/unpack_bundle.py D:\\eval-bundle.zip          (Windows)
  python train/unpack_bundle.py /mnt/d/eval-bundle.zip       (WSL2/Linux)

Ziele:
  eval/private/...        -> <Repo>/eval/private/...        (gitignored)
  research-agent/...      -> ~/.claude/skills/research-agent/...  (evals + references)
Bestehende Dateien werden ueberschrieben. Pfade mit ".." oder absolute Pfade werden abgelehnt.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent


def target_for(name: str, root: Path, skills: Path) -> Path | None:
    p = PurePosixPath(name)
    if p.is_absolute() or ".." in p.parts or not p.parts:
        raise ValueError(f"unsicherer Pfad im Paket: {name}")
    if p.parts[:2] == ("eval", "private"):
        return root.joinpath(*p.parts)
    if p.parts[0] == "research-agent":
        return skills.joinpath(*p.parts)
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("zip")
    ap.add_argument("--root", default=str(ROOT), help="Repo-Ordner (Standard: dieses Repo)")
    ap.add_argument("--skills-dir", default=str(Path.home() / ".claude" / "skills"))
    args = ap.parse_args()
    root, skills = Path(args.root), Path(args.skills_dir)
    counts = {"eval/private": 0, "research-agent": 0, "ignoriert": 0}
    with zipfile.ZipFile(args.zip) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            dest = target_for(info.filename, root, skills)
            if dest is None:
                counts["ignoriert"] += 1
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)
            counts["eval/private" if info.filename.startswith("eval/") else "research-agent"] += 1
    print(f"Ausgepackt: {counts['eval/private']} Dateien nach {root / 'eval' / 'private'}, "
          f"{counts['research-agent']} nach {skills / 'research-agent'}, ignoriert {counts['ignoriert']}")
    if not (root / "eval" / "private" / "replay").exists():
        print("Warnung: eval/private/replay fehlt, Offline-Messungen gehen dann nicht.", file=sys.stderr)


if __name__ == "__main__":
    main()
