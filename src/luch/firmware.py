"""Compare the radio's firmware with the newest MeshCore Companion release on GitHub."""
from __future__ import annotations

import asyncio
import json
import re
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

RELEASES = "https://api.github.com/repos/meshcore-dev/MeshCore/releases?per_page=40"
FLASHER = "https://meshcore.io/flasher"
CACHE_SECONDS = 6 * 3600


@dataclass
class Release:
    version: tuple[int, int, int]
    tag: str
    url: str


def parse_version(text: str) -> tuple[int, int, int] | None:
    m = re.search(r"v?(\d+)\.(\d+)(?:\.(\d+))?", text or "")
    return (int(m[1]), int(m[2]), int(m[3] or 0)) if m else None


def fmt(v: tuple[int, int, int]) -> str:
    return "v" + ".".join(map(str, v))


def newest_companion(releases: list[dict]) -> Release | None:
    best = None
    for r in releases:
        tag = r.get("tag_name", "")
        if r.get("prerelease") or r.get("draft") or not tag.startswith("companion-"):
            continue
        v = parse_version(tag)
        if v and (best is None or v > best.version):
            best = Release(v, tag, r.get("html_url", ""))
    return best


def _fetch() -> list[dict]:
    req = urllib.request.Request(RELEASES, headers={"User-Agent": "luch", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp)


async def latest_release(cache_file: Path) -> Release | None:
    """Newest Companion release, cached on disk so GitHub isn't asked on every start."""
    try:
        cached = json.loads(cache_file.read_text())
        if time.time() - cached["checked"] < CACHE_SECONDS:
            return Release(tuple(cached["version"]), cached["tag"], cached["url"])
    except (OSError, ValueError, KeyError):
        pass
    rel = newest_companion(await asyncio.to_thread(_fetch))
    if rel:
        cache_file.write_text(json.dumps(
            {"checked": int(time.time()), "version": rel.version, "tag": rel.tag, "url": rel.url}))
    return rel
