#!/usr/bin/env python3
"""Update WakaTime badges, OS, and AI sections in README.md."""

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
AI_START = "<!--START_SECTION:wakatime_ai-->"
AI_END = "<!--END_SECTION:wakatime_ai-->"
OS_START = "<!--START_SECTION:wakatime_os-->"
OS_END = "<!--END_SECTION:wakatime_os-->"

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


def api_get(
    api_key: str,
    api_url: str,
    *,
    timeout: float = 60.0,
    retries: int = 3,
    delay_seconds: float = 5.0,
) -> dict:
    request = urllib.request.Request(api_url)
    auth = base64.b64encode(api_key.encode("utf-8") + b":").decode("ascii")
    request.add_header("Authorization", f"Basic {auth}")
    last_error: Exception | None = None

    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise SystemExit(f"WakaTime API error {exc.code}: {body}") from exc
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            last_error = exc
            print(
                f"  API request failed ({attempt}/{retries}) {api_url}: {exc}"
            )
            if attempt < retries:
                time.sleep(delay_seconds)

    raise SystemExit(
        f"WakaTime API request failed after {retries} attempts: {last_error}"
    )


def shields_badge(label: str, message: str) -> str:
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
    """Fetch rank with retries — current_user can lag behind the board snapshot."""
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


def fetch_rank_safe(api_key: str, api_url: str) -> int | str:
    """Return rank, or 'keep' when it is null or the API is unreachable."""
    try:
        rank = fetch_rank(api_key, api_url)
    except SystemExit as exc:
        print(f"  soft-fail, keeping previous badge value: {exc}")
        return "keep"
    if rank is None:
        print("  null rank, keeping previous badge value")
        return "keep"
    return rank


def read_existing_rank(label: str) -> int | None:
    """Best-effort parse of current README badge value for soft-fail keep."""
    content = README.read_text(encoding="utf-8")
    encoded_label = urllib.parse.quote(label, safe="")
    pattern = re.compile(
        rf"{re.escape(encoded_label)}-(?:%23)?(\d+|Unranked)-",
        re.IGNORECASE,
    )
    match = pattern.search(content)
    if not match:
        return None
    value = match.group(1)
    if value.lower() == "unranked":
        return None
    return int(value)


def fetch_stats(api_key: str) -> dict:
    return (api_get(api_key, STATS_URL).get("data") or {})


def parse_daily_average(stats: dict) -> str | None:
    value = stats.get("human_readable_daily_average")
    if not value or not str(value).strip():
        return None
    return str(value).strip()


def parse_ai_stats(stats: dict) -> dict[str, str | None]:
    ai_category = None
    for item in stats.get("categories") or []:
        if str(item.get("name", "")).strip().lower() == "ai coding":
            ai_category = item
            break

    ai_time = None
    ai_share = None
    if ai_category:
        text = ai_category.get("text")
        if text:
            ai_time = str(text).strip()
        percent = ai_category.get("percent")
        if percent is not None:
            ai_share = f"{round(float(percent))}%"

    editor = None
    editors = stats.get("editors") or []
    if editors:
        top = max(editors, key=lambda e: float(e.get("total_seconds") or 0))
        name = top.get("name")
        if name:
            editor = str(name).strip()

    models: list[str] = []
    for item in stats.get("ai_model_breakdown") or []:
        name = str(item.get("name") or "").strip()
        if name:
            models.append(name)
    models = models[:2]

    stack = None
    if editor and models:
        stack = f"{editor} · {', '.join(models)}"
    elif editor:
        stack = editor
    elif models:
        stack = ", ".join(models)

    return {
        "ai_time": ai_time,
        "ai_share": ai_share,
        "stack": stack,
    }


def format_stat_message(item: dict | None) -> str:
    if not item:
        return "N/A"
    text = str(item.get("text") or "").strip() or "0 mins"
    percent = item.get("percent")
    if percent is None:
        return text
    return f"{text} · {round(float(percent))}%"


def parse_operating_systems(stats: dict) -> list[tuple[str, str]]:
    items = sorted(
        [i for i in (stats.get("operating_systems") or []) if isinstance(i, dict)],
        key=lambda i: float(i.get("total_seconds") or 0),
        reverse=True,
    )
    result: list[tuple[str, str]] = []
    for item in items:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        result.append((name, format_stat_message(item)))
    return result


def named_badges_markdown(
    entries: list[tuple[str, str]],
    alt_prefix: str,
    *,
    row_size: int | None = None,
) -> str:
    if not entries:
        return "  <!-- no data -->"

    def render(items: list[tuple[str, str]]) -> str:
        parts = []
        for label, message in items:
            alt = f"{alt_prefix}: {label} {message}"
            parts.append(badge_link(PROFILE_URL, shields_badge(label, message), alt))
        return "\n".join(parts)

    if not row_size or len(entries) <= row_size:
        return render(entries)

    rows = [
        entries[i : i + row_size] for i in range(0, len(entries), row_size)
    ]
    blocks = [render(row) for row in rows]
    return "\n</p>\n\n<p align=\"start\">\n".join(blocks)


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


def ai_badges_markdown(ai: dict[str, str | None]) -> str:
    parts = []
    specs = (
        ("AI Coding", ai.get("ai_time"), "WakaTime AI coding time"),
        ("AI Share", ai.get("ai_share"), "WakaTime AI share of coding time"),
        ("AI Stack", ai.get("stack"), "WakaTime AI stack"),
    )
    for label, value, alt_prefix in specs:
        message = value or "N/A"
        alt = f"{alt_prefix}: {message}"
        parts.append(badge_link(PROFILE_URL, shields_badge(label, message), alt))
    return "\n".join(parts)


def replace_section(content: str, start: str, end: str, inner: str) -> str:
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.DOTALL)
    if not pattern.search(content):
        raise SystemExit(f"Markers {start} / {end} not found in README.md")
    replacement = f"{start}\n{inner}\n  {end}"
    return pattern.sub(replacement, content, count=1)


def update_readme(
    daily_average: str | None,
    ranks: dict[str, int | None],
    ai: dict[str, str | None],
    operating_systems: list[tuple[str, str]],
) -> bool:
    content = README.read_text(encoding="utf-8")
    updated = replace_section(
        content, DAILY_START, DAILY_END, daily_badge_markdown(daily_average)
    )
    updated = replace_section(
        updated, RANK_START, RANK_END, rank_badges_markdown(ranks)
    )
    updated = replace_section(
        updated,
        OS_START,
        OS_END,
        named_badges_markdown(operating_systems, "WakaTime OS"),
    )
    updated = replace_section(
        updated, AI_START, AI_END, ai_badges_markdown(ai)
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

    stats = fetch_stats(api_key)
    daily_average = parse_daily_average(stats)
    ai = parse_ai_stats(stats)
    operating_systems = parse_operating_systems(stats)

    print(f"Daily average: {daily_average or 'unavailable'}")
    print(f"AI coding: {ai.get('ai_time') or 'unavailable'}")
    print(f"AI share: {ai.get('ai_share') or 'unavailable'}")
    print(f"AI stack: {ai.get('stack') or 'unavailable'}")
    print(
        "OS: "
        + (", ".join(f"{n} ({v})" for n, v in operating_systems) or "unavailable")
    )

    ranks: dict[str, int | None] = {}
    for board in LEADERBOARDS:
        result = fetch_rank_safe(api_key, board["api_url"])
        name = board["key"].capitalize()
        if result == "keep":
            kept = read_existing_rank(board["label"])
            ranks[board["key"]] = kept
            suffix = f"#{kept}" if kept is not None else "(unranked/unknown)"
            print(f"{name} rank: keep previous {suffix}")
            continue

        ranks[board["key"]] = result
        print(f"{name} rank: #{result}")

    changed = update_readme(daily_average, ranks, ai, operating_systems)
    print("README.md updated." if changed else "README.md already up to date.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
