"""Cloud review collector (MIL-194) — machine-independent redundancy for the
MIL daily fetch. Runs on GitHub Actions cron; accumulates deduped reviews into
data/{source}_{competitor}.json (git-scraping pattern).

Mirrors the exact record shapes + dedup keys of while-sleeping run_daily.py
Step 1 so ops/merge_cloud_fetch.py can fold these files into the local corpus
mechanically. No LLM calls, no PII beyond public review content.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("cloud_fetch")

DATA_DIR = Path(__file__).parent / "data"

COMPETITORS = {
    "natwest":  {"app_store_id": "334855322",  "google_play_id": "com.rbs.mobile.android.natwest"},
    "lloyds":   {"app_store_id": "469964520",  "google_play_id": "com.grppl.android.shell.CMBlloydsTSB73"},
    "hsbc":     {"app_store_id": "1220329065", "google_play_id": "uk.co.hsbc.hsbcukmobilebanking"},
    "monzo":    {"app_store_id": "1052238659", "google_play_id": "co.uk.getmondo"},
    "revolut":  {"app_store_id": "932493382",  "google_play_id": "com.revolut.revolut"},
    "barclays": {"app_store_id": "536248734",  "google_play_id": "com.barclays.android.barclaysmobilebanking"},
}

AS_MAX_PAGES = 5
AS_URL = "https://itunes.apple.com/gb/rss/customerreviews/page={page}/id={app_id}/sortBy=mostRecent/json"
GP_MAX_PAGES = 5
GP_PAGE_SIZE = 100


# --- dedup keys: byte-identical to run_daily.py -----------------------------

def key_app_store(rec: dict) -> str:
    return f"{rec.get('author','')}|{rec.get('date','')}|{rec.get('review','')[:80]}"


def key_google_play(rec: dict) -> str:
    return f"{rec.get('userName','')}|{rec.get('at','')}|{rec.get('content','')[:80]}"


# --- fetchers: same shapes as mil/harvester/sources -------------------------

def fetch_app_store(app_id: str, competitor: str) -> list[dict]:
    entries: list = []
    for page in range(1, AS_MAX_PAGES + 1):
        try:
            resp = requests.get(AS_URL.format(page=page, app_id=app_id), timeout=15)
            resp.raise_for_status()
            batch = resp.json().get("feed", {}).get("entry", [])
        except Exception as exc:
            logger.warning("[app_store] %s page=%d failed: %s — stopping", competitor, page, exc)
            break
        if page == 1 and batch and "im:name" in batch[0]:
            batch = batch[1:]
        if not batch:
            break
        entries.extend(batch)
    out = []
    for e in entries:
        try:
            out.append({
                "rating": int(e.get("im:rating", {}).get("label", 0)),
                "title": e.get("title", {}).get("label", ""),
                "review": e.get("content", {}).get("label", ""),
                "version": e.get("im:version", {}).get("label", ""),
                "date": e.get("updated", {}).get("label", ""),
                "author": e.get("author", {}).get("name", {}).get("label", ""),
            })
        except Exception:
            pass
    logger.info("[app_store] %s — %d parsed", competitor, len(out))
    return out


def fetch_google_play(package_id: str, competitor: str) -> list[dict]:
    from google_play_scraper import Sort, reviews

    raw: list = []
    token = None
    for page in range(1, GP_MAX_PAGES + 1):
        kwargs: dict = {"lang": "en", "country": "gb", "sort": Sort.NEWEST, "count": GP_PAGE_SIZE}
        if token is not None:
            kwargs["continuation_token"] = token
        try:
            result, token = reviews(package_id, **kwargs)
        except Exception as exc:
            logger.warning("[google_play] %s page=%d failed: %s — stopping", competitor, page, exc)
            break
        if not result:
            break
        raw.extend(result)
        if token is None or getattr(token, "token", None) is None:
            break
    out = []
    for item in raw:
        try:
            at = item.get("at", "")
            out.append({
                "rating": item.get("score", 3),
                "content": item.get("content", ""),
                "thumbsUpCount": item.get("thumbsUpCount", 0),
                "reviewCreatedVersion": item.get("reviewCreatedVersion", ""),
                "at": at.isoformat() if hasattr(at, "isoformat") else str(at),
                "userName": item.get("userName", ""),
            })
        except Exception:
            pass
    logger.info("[google_play] %s — %d parsed", competitor, len(out))
    return out


# --- accumulate --------------------------------------------------------------

def accumulate(source: str, competitor: str, fetched: list[dict], key_fn) -> int:
    DATA_DIR.mkdir(exist_ok=True)
    path = DATA_DIR / f"{source}_{competitor}.json"
    store = {"source": source, "competitor": competitor, "records": []}
    if path.exists():
        store = json.loads(path.read_text(encoding="utf-8"))
    existing = {key_fn(r) for r in store["records"]}
    new = [r for r in fetched if key_fn(r) not in existing]
    if new:
        store["records"].extend(new)
        store["record_count"] = len(store["records"])
        store["last_fetch_utc"] = datetime.now(timezone.utc).isoformat()
        path.write_text(json.dumps(store, indent=1, default=str), encoding="utf-8")
    logger.info("[%s] %s — %d fetched, %d new (total %d)",
                source, competitor, len(fetched), len(new), len(store["records"]))
    return len(new)


def main() -> int:
    total_new = 0
    failures = 0
    for comp, ids in COMPETITORS.items():
        try:
            total_new += accumulate("app_store", comp,
                                    fetch_app_store(ids["app_store_id"], comp), key_app_store)
        except Exception as exc:
            logger.error("[app_store] %s hard failure: %s", comp, exc)
            failures += 1
        try:
            total_new += accumulate("google_play", comp,
                                    fetch_google_play(ids["google_play_id"], comp), key_google_play)
        except Exception as exc:
            logger.error("[google_play] %s hard failure: %s", comp, exc)
            failures += 1
    logger.info("=== cloud fetch complete: %d new records, %d source failures ===",
                total_new, failures)
    # exit non-zero only if EVERY source failed (workflow shows red on total outage,
    # stays green on partial — App Store RSS flakiness is routine)
    return 1 if failures >= len(COMPETITORS) * 2 else 0


if __name__ == "__main__":
    sys.exit(main())
