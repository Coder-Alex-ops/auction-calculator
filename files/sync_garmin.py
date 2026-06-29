"""Sync Garmin Connect data (activities + wellness) to files or an HTTP ingest.

Thin wrapper around the open-source python-garminconnect library.
See: https://github.com/cyberjunky/python-garminconnect
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
from garminconnect import Garmin


TOKEN_DIR = Path.home() / ".garminconnect"


def _login_interactive() -> Garmin:
    email = os.environ.get("GARMIN_EMAIL")
    password = os.environ.get("GARMIN_PASSWORD")
    if not email or not password:
        sys.exit("Set GARMIN_EMAIL and GARMIN_PASSWORD before running --login.")
    api = Garmin(email=email, password=password, is_cn=False)
    api.login()
    TOKEN_DIR.mkdir(parents=True, exist_ok=True)
    api.garth.dump(str(TOKEN_DIR))
    return api


def _login_with_token_dir() -> Garmin:
    """Login using a saved token directory (no password needed)."""
    if not TOKEN_DIR.exists():
        sys.exit(
            f"No saved token at {TOKEN_DIR}. Run with --login once first "
            "(or set GARMIN_TOKEN_B64)."
        )
    api = Garmin()
    api.login(str(TOKEN_DIR))
    return api


def _restore_token_from_env() -> bool:
    """If GARMIN_TOKEN_B64 is set, write it out to TOKEN_DIR. Returns True if used."""
    blob = os.environ.get("GARMIN_TOKEN_B64")
    if not blob:
        return False
    TOKEN_DIR.mkdir(parents=True, exist_ok=True)
    raw = base64.b64decode(blob)
    bundle = json.loads(raw.decode("utf-8"))
    for name, contents in bundle.items():
        (TOKEN_DIR / name).write_text(contents)
    return True


def _print_token_bundle() -> None:
    """Print the saved token files as a single base64 JSON blob for CI secrets."""
    if not TOKEN_DIR.exists():
        sys.exit(f"No token files found at {TOKEN_DIR}.")
    bundle: dict[str, str] = {}
    for path in sorted(TOKEN_DIR.iterdir()):
        if path.is_file():
            bundle[path.name] = path.read_text()
    encoded = base64.b64encode(json.dumps(bundle).encode("utf-8")).decode("ascii")
    print("\n--- GARMIN_TOKEN_B64 (copy everything between the lines) ---")
    print(encoded)
    print("--- end token bundle ---")


def _date_range(days: int) -> list[date]:
    today = date.today()
    return [today - timedelta(days=i) for i in range(days - 1, -1, -1)]


def _safe(d: dict | None, *keys: str) -> Any:
    cur: Any = d
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def _fmt_minutes_as_hours(minutes: int | float | None) -> str | None:
    if minutes is None:
        return None
    return f"{minutes / 60:.1f}"


def fetch_wellness(api: Garmin, day: date) -> dict[str, Any]:
    iso = day.isoformat()
    out: dict[str, Any] = {"date": iso}

    def _try(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:
            return None

    stats = _try(api.get_stats, iso) or {}
    sleep = _try(api.get_sleep_data, iso) or {}
    hrv = _try(api.get_hrv_data, iso) or {}
    readiness = _try(api.get_training_readiness, iso) or []
    body_battery = _try(api.get_body_battery, iso) or []

    out["resting_hr"] = stats.get("restingHeartRate")
    out["steps"] = stats.get("totalSteps")
    out["stress_avg"] = stats.get("averageStressLevel")

    sleep_dto = sleep.get("dailySleepDTO") or {}
    sleep_seconds = sleep_dto.get("sleepTimeSeconds")
    out["sleep_hours"] = round(sleep_seconds / 3600, 1) if sleep_seconds else None
    out["sleep_score"] = _safe(sleep_dto, "sleepScores", "overall", "value")

    out["hrv_overnight_ms"] = _safe(hrv, "hrvSummary", "lastNightAvg")

    if isinstance(readiness, list) and readiness:
        out["training_readiness"] = readiness[0].get("score")
    else:
        out["training_readiness"] = None

    bb_min = bb_max = None
    if isinstance(body_battery, list) and body_battery:
        entry = body_battery[0]
        values = entry.get("bodyBatteryValuesArray") or []
        levels = [v[1] for v in values if isinstance(v, list) and len(v) >= 2 and v[1] is not None]
        if levels:
            bb_min, bb_max = min(levels), max(levels)
    out["body_battery_min"] = bb_min
    out["body_battery_max"] = bb_max

    return out


def fetch_activities(api: Garmin, days: int) -> list[dict[str, Any]]:
    try:
        raw = api.get_activities(0, days * 4) or []
    except Exception:
        return []
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=days - 1)
    keep: list[dict[str, Any]] = []
    for act in raw:
        started = act.get("startTimeGMT") or act.get("startTimeLocal")
        if not started:
            continue
        try:
            started_dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
        except ValueError:
            continue
        if started_dt.date() < cutoff:
            continue
        keep.append(
            {
                "id": act.get("activityId"),
                "name": act.get("activityName"),
                "type": _safe(act, "activityType", "typeKey"),
                "start": started,
                "duration_s": act.get("duration"),
                "distance_m": act.get("distance"),
                "avg_hr": act.get("averageHR"),
                "max_hr": act.get("maxHR"),
                "calories": act.get("calories"),
                "elevation_gain_m": act.get("elevationGain"),
                "training_load": act.get("activityTrainingLoad"),
            }
        )
    return keep


def render_wellness_md(w: dict[str, Any]) -> str:
    lines = [f"# Garmin wellness {w['date']}"]
    if w.get("resting_hr") is not None:
        lines.append(f"- Resting HR: {w['resting_hr']} bpm")
    if w.get("hrv_overnight_ms") is not None:
        lines.append(f"- HRV (overnight): {w['hrv_overnight_ms']} ms")
    if w.get("sleep_hours") is not None:
        score = f" (score {w['sleep_score']})" if w.get("sleep_score") is not None else ""
        lines.append(f"- Sleep: {w['sleep_hours']} h{score}")
    if w.get("body_battery_min") is not None and w.get("body_battery_max") is not None:
        lines.append(f"- Body battery: {w['body_battery_min']} -> {w['body_battery_max']}")
    if w.get("stress_avg") is not None:
        lines.append(f"- Stress (avg): {w['stress_avg']}")
    if w.get("steps") is not None:
        lines.append(f"- Steps: {w['steps']}")
    if w.get("training_readiness") is not None:
        lines.append(f"- Training readiness: {w['training_readiness']}")
    return "\n".join(lines) + "\n"


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(text: str | None) -> str:
    if not text:
        return "activity"
    return _SLUG_RE.sub("-", text.lower()).strip("-") or "activity"


def render_activity_md(act: dict[str, Any]) -> str:
    start = act.get("start") or ""
    lines = [f"# {act.get('name') or 'Activity'} ({act.get('type') or 'unknown'})"]
    lines.append(f"- Start: {start}")
    if act.get("duration_s"):
        lines.append(f"- Duration: {act['duration_s'] / 60:.1f} min")
    if act.get("distance_m"):
        lines.append(f"- Distance: {act['distance_m'] / 1000:.2f} km")
    if act.get("avg_hr"):
        lines.append(f"- Avg HR: {act['avg_hr']} bpm")
    if act.get("max_hr"):
        lines.append(f"- Max HR: {act['max_hr']} bpm")
    if act.get("calories"):
        lines.append(f"- Calories: {act['calories']}")
    if act.get("elevation_gain_m"):
        lines.append(f"- Elevation gain: {act['elevation_gain_m']} m")
    if act.get("training_load"):
        lines.append(f"- Training load: {act['training_load']}")
    return "\n".join(lines) + "\n"


def write_files(out_dir: Path, wellness: list[dict], activities: list[dict]) -> None:
    daily_dir = out_dir / "daily"
    acts_dir = out_dir / "activities"
    daily_dir.mkdir(parents=True, exist_ok=True)
    acts_dir.mkdir(parents=True, exist_ok=True)

    for w in wellness:
        (daily_dir / f"{w['date']}.md").write_text(render_wellness_md(w))

    for act in activities:
        day = (act.get("start") or "")[:10] or "unknown"
        name = _slug(act.get("name"))
        (acts_dir / f"{day}-{name}.md").write_text(render_activity_md(act))

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "wellness": wellness,
        "activities": activities,
    }
    (out_dir / "data.json").write_text(json.dumps(payload, indent=2, default=str))


def post_to_ingest(wellness: list[dict], activities: list[dict]) -> None:
    url = os.environ.get("GARMIN_INGEST_URL")
    secret = os.environ.get("GARMIN_INGEST_SECRET") or os.environ.get("SESSION_LOG_SECRET")
    if not url or not secret:
        sys.exit("Set GARMIN_INGEST_URL and GARMIN_INGEST_SECRET for --sink supabase.")
    resp = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {secret}",
            "Content-Type": "application/json",
        },
        json={"activities": activities, "wellness": wellness},
        timeout=30,
    )
    resp.raise_for_status()
    print(f"POST {url} -> {resp.status_code}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync Garmin Connect data.")
    parser.add_argument("--login", action="store_true", help="Log in and save token bundle.")
    parser.add_argument("--days", type=int, default=3, help="How many days back to fetch.")
    parser.add_argument("--sink", choices=["files", "supabase"], default="files")
    parser.add_argument("--out", default="./garmin", help="Output folder for files sink.")
    parser.add_argument("--dry-run", action="store_true", help="Print results, don't write.")
    args = parser.parse_args()

    if args.login:
        _login_interactive()
        _print_token_bundle()
        return

    _restore_token_from_env()
    api = _login_with_token_dir()

    wellness = [fetch_wellness(api, d) for d in _date_range(args.days)]
    activities = fetch_activities(api, args.days)

    if args.dry_run:
        print(json.dumps({"wellness": wellness, "activities": activities}, indent=2, default=str))
        return

    if args.sink == "files":
        write_files(Path(args.out), wellness, activities)
        print(f"Wrote {len(wellness)} wellness notes and {len(activities)} activities to {args.out}")
    else:
        post_to_ingest(wellness, activities)


if __name__ == "__main__":
    main()
