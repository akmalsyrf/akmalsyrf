#!/usr/bin/env python3
"""Update WakaTime Indonesia + global rank shields badges in README.md."""

from __future__ import annotations

import base64
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"
ENV_FILE = ROOT / ".env"
START = "<!--START_SECTION:wakatime_rank-->"
END = "<!--END_SECTION:wakatime_rank-->"

LEADERBOARDS = (
    {
        "key": "indonesia",
        "label": "Indonesia%20Rank",
        "alt_prefix": "WakaTime Indonesia Rank",
        "api_url": "https://wakatime.com/api/v1/leaders?country_code=ID",
        "page_url": "https://wakatime.com/leaders/?country_code=ID",
    },
    {
        "key": "global",
        "label": "Global%20Rank",
        "alt_prefix": "WakaTime Global Rank",
        "api_url": "https://wakatime.com/api/v1/leaders",
        "page_url": "https://wakatime.com/leaders",
    },
)


def load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def fetch_rank(api_key: str, api_url: str) -> int | None:
    request = urllib.request.Request(api_url)
    auth = base64.b64encode(api_key.encode("utf-8") + b":").decode("ascii")
    request.add_header("Authorization", f"Basic {auth}")

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"WakaTime API error {exc.code}: {body}") from exc

    current_user = payload.get("current_user") or {}
    rank = current_user.get("rank")
    return int(rank) if rank is not None else None


def badge_markdown(label: str, alt_prefix: str, page_url: str, rank: int | None) -> str:
    value = f"%23{rank}" if rank is not None else "Unranked"
    alt = f"{alt_prefix} #{rank}" if rank is not None else f"{alt_prefix} Unranked"
    src = (
        "https://img.shields.io/badge/"
        f"{label}-{value}-black?logo=wakatime&logoColor=white"
    )
    return (
        f'  <a href="{page_url}">\n'
        f'    <img src="{src}" alt="{alt}" />\n'
        f"  </a>"
    )


def badges_markdown(ranks: dict[str, int | None]) -> str:
    parts = [
        badge_markdown(
            board["label"],
            board["alt_prefix"],
            board["page_url"],
            ranks[board["key"]],
        )
        for board in LEADERBOARDS
    ]
    return "\n".join(parts)


def update_readme(ranks: dict[str, int | None]) -> bool:
    content = README.read_text(encoding="utf-8")
    pattern = re.compile(
        re.escape(START) + r".*?" + re.escape(END),
        re.DOTALL,
    )
    if not pattern.search(content):
        raise SystemExit(f"Markers {START} / {END} not found in README.md")

    replacement = f"{START}\n{badges_markdown(ranks)}\n  {END}"
    updated = pattern.sub(replacement, content, count=1)
    if updated == content:
        return False
    README.write_text(updated, encoding="utf-8")
    return True


def main() -> int:
    load_dotenv(ENV_FILE)
    api_key = os.environ.get("WAKATIME_API_KEY", "").strip()
    if not api_key:
        raise SystemExit(
            "WAKATIME_API_KEY is required (.env or environment / GitHub secret)"
        )

    ranks: dict[str, int | None] = {}
    for board in LEADERBOARDS:
        ranks[board["key"]] = fetch_rank(api_key, board["api_url"])
        rank = ranks[board["key"]]
        name = board["key"].capitalize()
        if rank is None:
            print(f"{name} rank: unranked")
        else:
            print(f"{name} rank: #{rank}")

    changed = update_readme(ranks)
    print("README.md updated." if changed else "README.md already up to date.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
