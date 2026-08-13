#!/usr/bin/env python3
"""Update Duolingo streak / XP / learning badges in README.md."""

from __future__ import annotations

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

DUO_START = "<!--START_SECTION:duolingo-->"
DUO_END = "<!--END_SECTION:duolingo-->"
DUOLINGO_API = "https://www.duolingo.com/2017-06-30"
LEADERBOARD_API = (
    "https://duolingo-leaderboards-prod.duolingo.com"
    "/leaderboards/7d9f5dd1-8423-491a-91f2-2532052038ce"
)

# Duolingo weekly leagues are 0-indexed in the API (0=Bronze … 9=Diamond)
LEAGUE_TIERS = (
    "Bronze",
    "Silver",
    "Gold",
    "Sapphire",
    "Ruby",
    "Emerald",
    "Amethyst",
    "Pearl",
    "Obsidian",
    "Diamond",
)

LANGUAGE_NAMES = {
    "en": "English",
    "ja": "Japanese",
    "id": "Indonesian",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "ko": "Korean",
    "zh": "Chinese",
    "pt": "Portuguese",
    "it": "Italian",
    "ru": "Russian",
    "hi": "Hindi",
    "ar": "Arabic",
    "tr": "Turkish",
    "vi": "Vietnamese",
}


def load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def language_label(code: str | None) -> str | None:
    if not code:
        return None
    code = code.strip().lower()
    return LANGUAGE_NAMES.get(code, code.upper())


def duo_headers(jwt: str | None = None) -> dict[str, str]:
    headers = {
        "User-Agent": "akmalsyrf-github-profile",
        "Accept": "application/json",
    }
    if jwt:
        headers["Authorization"] = f"Bearer {jwt}"
    return headers


def http_get_json(url: str, headers: dict[str, str], *, timeout: float = 45.0) -> dict:
    request = urllib.request.Request(url, headers=headers)
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise SystemExit(f"Duolingo API error {exc.code}: {body[:300]}") from exc
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            last_error = exc
            print(f"  Duolingo request failed ({attempt}/3) {url}: {exc}")
            if attempt < 3:
                time.sleep(5)
    raise SystemExit(f"Duolingo API request failed after retries: {last_error}")


def resolve_streak(user: dict) -> int:
    streak = user.get("streak")
    if isinstance(streak, int) and streak > 0:
        return streak

    current = (user.get("streakData") or {}).get("currentStreak")
    if isinstance(current, int):
        return current
    if isinstance(current, dict) and isinstance(current.get("length"), int):
        return int(current["length"])
    if isinstance(streak, int):
        return streak
    return 0


def format_xp(value: int) -> str:
    return f"{value:,}"


def weekly_xp_from_summaries(summaries: list[dict]) -> int | None:
    if not summaries:
        return None
    # last 7 entries by date
    ordered = sorted(summaries, key=lambda s: int(s.get("date") or 0))[-7:]
    total = sum(int(s.get("gainedXp") or 0) for s in ordered)
    return total


def league_name(tier: int | None) -> str | None:
    if tier is None or tier < 0 or tier >= len(LEAGUE_TIERS):
        return None
    return LEAGUE_TIERS[tier]


def fetch_league_standing(
    user_id: int, jwt: str
) -> tuple[int | None, str | None]:
    """Return (rank_1_based, league_name) for the active weekly XP league."""
    payload = http_get_json(
        f"{LEADERBOARD_API}/users/{user_id}?client_unlocked=true",
        duo_headers(jwt),
    )
    tier = payload.get("tier")
    if not isinstance(tier, int):
        tracking_tier = None
        try:
            tracking = http_get_json(
                f"{DUOLINGO_API}/users/{user_id}?fields=trackingProperties",
                duo_headers(jwt),
            )
            raw = (tracking.get("trackingProperties") or {}).get(
                "leaderboard_league"
            )
            if isinstance(raw, int):
                tracking_tier = raw
        except SystemExit:
            tracking_tier = None
        tier = tracking_tier

    active = payload.get("active") or {}
    rankings = ((active.get("cohort") or {}).get("rankings")) or []
    rank: int | None = None
    for index, entry in enumerate(rankings):
        if int(entry.get("user_id") or -1) == int(user_id):
            rank = index + 1
            break

    return rank, league_name(tier if isinstance(tier, int) else None)


def fetch_duolingo_stats(username: str, jwt: str | None) -> dict[str, str | None]:
    public = http_get_json(
        f"{DUOLINGO_API}/users?username={urllib.parse.quote(username)}",
        duo_headers(),
    )
    users = public.get("users") or []
    if not users:
        raise SystemExit(f"Duolingo user not found: {username}")

    user = users[0]
    user_id = user.get("id")
    authenticated = False
    weekly_xp: int | None = None
    league_rank: int | None = None
    league: str | None = None

    if jwt and user_id is not None:
        try:
            auth_user = http_get_json(
                f"{DUOLINGO_API}/users/{user_id}",
                duo_headers(jwt),
            )
            if auth_user.get("username"):
                user = auth_user
                authenticated = True
                summaries_payload = http_get_json(
                    f"{DUOLINGO_API}/users/{user_id}/xp_summaries?last=30",
                    duo_headers(jwt),
                )
                weekly_xp = weekly_xp_from_summaries(
                    list(summaries_payload.get("summaries") or [])
                )
                if weekly_xp is None and isinstance(user.get("weeklyXp"), int):
                    weekly_xp = int(user["weeklyXp"])
                try:
                    league_rank, league = fetch_league_standing(int(user_id), jwt)
                except SystemExit as league_exc:
                    print(f"  League standing soft-failed: {league_exc}")
        except SystemExit as exc:
            print(f"  JWT path soft-failed, using public profile: {exc}")

    courses = user.get("courses") or []
    course_xp = sum(int(c.get("xp") or 0) for c in courses if isinstance(c, dict))
    total_xp = user.get("totalXp")
    if not isinstance(total_xp, int):
        total_xp = course_xp

    learning = None
    current_course = user.get("currentCourse") or {}
    if isinstance(current_course, dict):
        title = (current_course.get("title") or "").strip()
        if title:
            learning = title
        else:
            learning = language_label(current_course.get("learningLanguage"))
    if not learning:
        learning = language_label(user.get("learningLanguage"))
    if not learning and courses:
        top = max(courses, key=lambda c: int(c.get("xp") or 0))
        learning = (top.get("title") or "").strip() or language_label(
            top.get("learningLanguage")
        )

    profile_url = f"https://www.duolingo.com/profile/{user.get('username') or username}"
    league_badge = None
    if league_rank is not None and league:
        league_badge = f"#{league_rank} {league}"
    elif league_rank is not None:
        league_badge = f"#{league_rank}"
    elif league:
        league_badge = league

    return {
        "profile_url": profile_url,
        "streak": str(resolve_streak(user)),
        "total_xp": format_xp(int(total_xp)),
        "weekly_xp": format_xp(weekly_xp) if weekly_xp is not None else None,
        "learning": learning,
        "league": league_badge,
        "authenticated": "yes" if authenticated else "no",
    }


def shields_badge(label: str, message: str, color: str = "58CC02") -> str:
    def enc(text: str) -> str:
        return (
            urllib.parse.quote(text, safe="")
            .replace("-", "--")
            .replace("_", "__")
        )

    return (
        "https://img.shields.io/badge/"
        f"{enc(label)}-{enc(message)}-{color}?logo=duolingo&logoColor=white"
    )


def badge_link(href: str, src: str, alt: str) -> str:
    return (
        f'  <a href="{href}">\n'
        f'    <img src="{src}" alt="{alt}" />\n'
        f"  </a>"
    )


def duolingo_badges_markdown(stats: dict[str, str | None]) -> str:
    href = stats["profile_url"] or "https://www.duolingo.com"
    streak = stats.get("streak") or "0"
    total_xp = stats.get("total_xp") or "0"
    learning = stats.get("learning") or "N/A"
    weekly = stats.get("weekly_xp")
    league = stats.get("league")

    row1 = [
        badge_link(
            href,
            shields_badge("Streak", f"{streak} days"),
            f"Duolingo streak: {streak} days",
        ),
        badge_link(
            href,
            shields_badge("Total XP", total_xp),
            f"Duolingo total XP: {total_xp}",
        ),
    ]
    if weekly is not None:
        row1.append(
            badge_link(
                href,
                shields_badge("Weekly XP", weekly),
                f"Duolingo weekly XP: {weekly}",
            )
        )

    row2 = []
    if league is not None:
        row2.append(
            badge_link(
                href,
                shields_badge("League Rank", league),
                f"Duolingo league rank: {league}",
            )
        )
    row2.append(
        badge_link(
            href,
            shields_badge("Learning", learning),
            f"Duolingo learning: {learning}",
        )
    )

    return "\n".join(row1) + "\n</p>\n\n<p align=\"start\">\n" + "\n".join(row2)


def replace_section(content: str, start: str, end: str, inner: str) -> str:
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.DOTALL)
    if not pattern.search(content):
        raise SystemExit(f"Markers {start} / {end} not found in README.md")
    replacement = f"{start}\n{inner}\n  {end}"
    return pattern.sub(replacement, content, count=1)


def update_readme(stats: dict[str, str | None]) -> bool:
    content = README.read_text(encoding="utf-8")
    updated = replace_section(
        content, DUO_START, DUO_END, duolingo_badges_markdown(stats)
    )
    if updated == content:
        return False
    README.write_text(updated, encoding="utf-8")
    return True


def main() -> int:
    load_dotenv(ENV_FILE)
    username = os.environ.get("DUOLINGO_USERNAME", "").strip()
    jwt = os.environ.get("DUOLINGO_JWT", "").strip() or None

    if not username:
        print("DUOLINGO_USERNAME not set — skipping Duolingo section update.")
        return 0

    stats = fetch_duolingo_stats(username, jwt)
    print(f"Duolingo user: {username}")
    print(f"Authenticated: {stats['authenticated']}")
    print(f"Streak: {stats['streak']} days")
    print(f"Total XP: {stats['total_xp']}")
    print(f"Weekly XP: {stats.get('weekly_xp') or 'n/a'}")
    print(f"League rank: {stats.get('league') or 'n/a'}")
    print(f"Learning: {stats.get('learning') or 'n/a'}")

    changed = update_readme(stats)
    print("README.md updated." if changed else "README.md already up to date.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
