#!/usr/bin/env python3
"""Update WakaTime daily average + Indonesia/global rank badges in README.md."""

from __future__ import annotations

import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"
ENV_FILE = ROOT / ".env"

RANK_START = "<!--START_SECTION:wakatime_rank-->"
RANK_END = "<!--END_SECTION:wakatime_rank-->"
DAILY_START = "<!--START_SECTION:wakatime_daily-->"
DAILY_END = "<!--END_SECTION:wakatime_daily-->"

STATS_URL = "https://wakatime.com/api/v1/users/current/stats/last_7_days"
PROFILE_URL = "https://wakatime.com/@018cc9d5-ad5f-499e-a91c-eec6ac3ebfcf"

LEADERBOARDS = (
    {
        "key": "indonesia",
        "label": "Indonesia Rank",
        "alt_prefix": "WakaTime Indonesia Rank",
        "api_url": "https://wakatime.com/api/v1/leaders?country_code=ID",
        "page_url": "https://wakatime.com/leaders/?country_code=ID",
    },
    {
        "key": "global",
        "label": "Global Rank",
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


def api_get(api_key: str, api_url: str) -> dict:
    request = urllib.request.Request(api_url)
    auth = base64.b64encode(api_key.encode("utf-8") + b":").decode("ascii")
    request.add_header("Authorization", f"Basic {auth}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"WakaTime API error {exc.code}: {body}") from exc


def shields_badge(label: str, message: str) -> str:
    # shields.io static badge path encoding
    def enc(text: str) -> str:
        return (
            urllib.parse.quote(text, safe="")
            .replace("-", "--")
            .replace("_", "__")
        )

    return (
        "https://img.shields.io/badge/"
        f"{enc(label)}-{enc(message)}-black?logo=wakatime&logoColor=white"
    )


def badge_link(href: str, src: str, alt: str) -> str:
    return (
        f'  <a href="{href}">\n'
        f'    <img src="{src}" alt="{alt}" />\n'
        f"  </a>"
    )


def fetch_rank_once(api_key: str, api_url: str) -> tuple[int | None, dict]:
    payload = api_get(api_key, api_url)
    current_user = payload.get("current_user") or {}
    rank = current_user.get("rank")
    meta = {
        "rank": rank,
        "user_page": current_user.get("page"),
        "response_page": payload.get("page"),
        "total_pages": payload.get("total_pages"),
        "country_code": payload.get("country_code"),
        "modified_at": payload.get("modified_at"),
        "range": (payload.get("range") or {}).get("text"),
    }
    return (int(rank) if rank is not None else None, meta)


def fetch_rank(
    api_key: str,
    api_url: str,
    *,
    retries: int = 3,
    delay_seconds: float = 20.0,
) -> int | None:
    """Fetch rank with retries — country boards can briefly return null mid-refresh."""
    last_meta: dict = {}
    for attempt in range(1, retries + 1):
        rank, last_meta = fetch_rank_once(api_key, api_url)
        if rank is not None:
            if attempt > 1:
                print(f"  recovered on attempt {attempt}: {last_meta}")
            return rank
        print(f"  attempt {attempt}/{retries} returned null rank: {last_meta}")
        if attempt < retries:
            time.sleep(delay_seconds)
    return None


def fetch_daily_average(api_key: str) -> str | None:
    payload = api_get(api_key, STATS_URL)
    data = payload.get("data") or {}
    value = data.get("human_readable_daily_average")
    if not value or not str(value).strip():
        return None
    return str(value).strip()


def rank_badges_markdown(ranks: dict[str, int | None]) -> str:
    parts = []
    for board in LEADERBOARDS:
        rank = ranks[board["key"]]
        message = f"#{rank}" if rank is not None else "Unranked"
        alt = (
            f"{board['alt_prefix']} #{rank}"
            if rank is not None
            else f"{board['alt_prefix']} Unranked"
        )
        src = shields_badge(board["label"], message)
        parts.append(badge_link(board["page_url"], src, alt))
    return "\n".join(parts)


def daily_badge_markdown(daily_average: str | None) -> str:
    message = daily_average or "N/A"
    alt = (
        f"WakaTime daily average {daily_average}"
        if daily_average
        else "WakaTime daily average unavailable"
    )
    src = shields_badge("Daily Average", message)
    return badge_link(PROFILE_URL, src, alt)


def replace_section(content: str, start: str, end: str, inner: str) -> str:
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.DOTALL)
    if not pattern.search(content):
        raise SystemExit(f"Markers {start} / {end} not found in README.md")
    replacement = f"{start}\n{inner}\n  {end}"
    return pattern.sub(replacement, content, count=1)


def update_readme(daily_average: str | None, ranks: dict[str, int | None]) -> bool:
    content = README.read_text(encoding="utf-8")
    updated = replace_section(
        content, DAILY_START, DAILY_END, daily_badge_markdown(daily_average)
    )
    updated = replace_section(
        updated, RANK_START, RANK_END, rank_badges_markdown(ranks)
    )
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

    daily_average = fetch_daily_average(api_key)
    if daily_average:
        print(f"Daily average: {daily_average}")
    else:
        print("Daily average: unavailable")

    ranks: dict[str, int | None] = {}
    for board in LEADERBOARDS:
        ranks[board["key"]] = fetch_rank(api_key, board["api_url"])
        rank = ranks[board["key"]]
        name = board["key"].capitalize()
        if rank is None:
            print(f"{name} rank: unranked")
        else:
            print(f"{name} rank: #{rank}")

    changed = update_readme(daily_average, ranks)
    print("README.md updated." if changed else "README.md already up to date.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
