# AECOM Research & Web Summarizer Agent

A configurable local agent that continuously scans web/RSS signals and produces regular + urgent intelligence reports tailored to:

- **Company:** AECOM
- **Role:** Senior Director Operational AI & Agentic Automation

## What it does

- Runs research on a configurable interval (e.g., daily).
- Produces reports on a separate configurable interval (e.g., weekly).
- Triggers an **urgent report** immediately when high-urgency signals appear.
- Groups findings by topic (AI, AEC industry, operations/back office, supply chain, talent, leadership).
- Adds priority bullets for **operational, tactical, strategic** timeframes.
- Includes a table of quantifiable KPIs (currently stock indicators; configurable).
- Archives reports locally with file names indicating regular vs urgent report and date.

## Files

- `research_agent.py` – main agent.
- `config.json` – runtime configuration (auto-created on first run if missing).
- `reports/` – output reports archive.
- `agent_state.json` – scheduling/seen-items state.
- `research_cache.jsonl` – cached collected signals.

## Quick start

```bash
python3 research_agent.py --run-once --force-report
```

This will:
1. Create `config.json` if absent.
2. Pull signals from configured RSS sources.
3. Generate a regular report immediately.

Run continuously:

```bash
python3 research_agent.py
```

## Configuration

Edit `config.json`:

- `research_interval_hours`: research cadence.
- `report_interval_hours`: regular report cadence.
- `urgent_threshold`: urgency score threshold for ad-hoc urgent report.
- `output_dir`: report archive path.
- `sources.rss`: feed list.
- `topic_rules`: topic keyword logic.
- `critical_keywords`: urgency boost terms.
- `kpi.stocks`: ticker symbols for KPI table (Stooq format).

### Report naming

- Regular: `regular_report_YYYYMMDD.md`
- Urgent: `urgent_report_YYYYMMDD.md`

## Notes

- Source retrieval is best-effort; failed feeds are skipped.
- KPI fetch uses Stooq CSV endpoint when available.
- Relevance/urgency scores are heuristic and intended to be tuned in config.
