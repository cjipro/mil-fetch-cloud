# mil-fetch-cloud

Machine-independent daily review collector (MIL-194) — redundancy for the
while-sleeping MIL fetch so daily signal survives the primary machine being off.

- `cloud_fetch.py` pulls App Store (iTunes RSS, 5 pages) + Google Play
  (google-play-scraper, 5×100) for the 6 monitored UK banking apps and
  accumulates deduped records into `data/{source}_{competitor}.json`.
- `.github/workflows/fetch.yml` runs it daily at 06:40 UTC and commits the
  delta (git-scraping pattern). Manual fire: Actions → daily-fetch → Run workflow.
- Record shapes + dedup keys mirror `run_daily.py` Step 1 exactly; fold into
  the local corpus with `ops/merge_cloud_fetch.py` in while-sleeping.

**PRIVATE by design** — accumulates a public-review corpus that must not be
republished. No LLM calls, no secrets required (GITHUB_TOKEN default perms).
