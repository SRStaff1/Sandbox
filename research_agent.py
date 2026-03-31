#!/usr/bin/env python3
"""Research + web summarizer agent for AECOM operational AI leadership.

The agent can run in scheduled mode or one-shot mode. It gathers RSS signals,
filters them by configured relevance, scores urgency/impact, and writes regular
or urgent reports to an archive directory.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import textwrap
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_CONFIG = {
    "organization": "AECOM",
    "role": "Senior Director Operational AI & Agentic Automation",
    "output_dir": "./reports",
    "state_file": "./agent_state.json",
    "research_cache_file": "./research_cache.jsonl",
    "research_interval_hours": 24,
    "report_interval_hours": 168,
    "lookback_days_for_report": 14,
    "max_items_per_topic": 8,
    "urgent_threshold": 80,
    "sources": {
        "rss": [
            "https://feeds.feedburner.com/oreilly/radar",
            "https://www.technologyreview.com/feed/",
            "https://www.artificialintelligence-news.com/feed/",
            "https://www.wsj.com/xml/rss/3_7031.xml",
            "https://www.mckinsey.com/featured-insights/artificial-intelligence/rss.xml",
            "https://www.weforum.org/agenda/feed/",
        ]
    },
    "topic_rules": {
        "AI & Automation": [
            "ai",
            "artificial intelligence",
            "agentic",
            "automation",
            "copilot",
            "llm",
            "model",
        ],
        "AECOM / AEC Industry": [
            "aecom",
            "engineering",
            "construction",
            "infrastructure",
            "architecture",
            "transportation",
            "water",
            "energy transition",
        ],
        "Operations & Back Office": [
            "erp",
            "finance",
            "procurement",
            "back office",
            "workflow",
            "shared services",
            "operations",
        ],
        "Supply Chain & Customers": [
            "supply chain",
            "logistics",
            "supplier",
            "commodity",
            "client",
            "public sector",
        ],
        "Talent & Workforce": [
            "hiring",
            "recruitment",
            "skills",
            "reskilling",
            "productivity",
            "layoff",
            "labor",
            "workforce",
        ],
        "Leadership & Career Signals": [
            "leadership",
            "director",
            "executive",
            "governance",
            "risk",
            "board",
            "strategy",
        ],
    },
    "critical_keywords": [
        "regulation",
        "ban",
        "breach",
        "cyberattack",
        "lawsuit",
        "safety incident",
        "shutdown",
        "recession",
        "rate hike",
        "export control",
    ],
    "relevance_dimensions": {
        "aec_industry_impact": {
            "weight": 1.4,
            "keywords": [
                "infrastructure",
                "engineering",
                "construction",
                "transportation",
                "water",
                "energy",
                "public sector",
                "federal funding",
            ],
        },
        "enterprise_autonomization": {
            "weight": 1.5,
            "keywords": [
                "agentic",
                "automation",
                "workflow",
                "erp",
                "back office",
                "shared services",
                "copilot",
            ],
        },
        "delivery_process_impact": {
            "weight": 1.2,
            "keywords": [
                "project delivery",
                "productivity",
                "quality",
                "pm tool",
                "software delivery",
                "devops",
            ],
        },
        "talent_and_workforce": {
            "weight": 1.1,
            "keywords": [
                "hiring",
                "recruitment",
                "reskilling",
                "layoff",
                "labor",
                "workforce",
            ],
        },
        "leadership_and_risk": {
            "weight": 1.1,
            "keywords": [
                "governance",
                "compliance",
                "regulation",
                "risk",
                "security",
                "executive",
            ],
        },
    },
    "kpi": {
        "stocks": ["ACM.US", "MSFT.US", "GOOGL.US", "NVDA.US", "PLTR.US"],
    },
}


@dataclass
class Signal:
    id: str
    title: str
    link: str
    published: str
    source: str
    summary: str
    topics: list[str]
    relevance_score: int
    urgency_score: int
    dimension_scores: dict[str, int] = field(default_factory=dict)
    rationale: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Signal":
        return cls(
            id=raw.get("id", ""),
            title=raw.get("title", ""),
            link=raw.get("link", ""),
            published=raw.get("published", ""),
            source=raw.get("source", ""),
            summary=raw.get("summary", ""),
            topics=raw.get("topics", []) or ["General Technology Signals"],
            relevance_score=int(raw.get("relevance_score", 0)),
            urgency_score=int(raw.get("urgency_score", 0)),
            dimension_scores=raw.get("dimension_scores", {}) or {},
            rationale=raw.get("rationale", []) or [],
        )


class ResearchAgent:
    def __init__(self, config_path: Path):
        self.config_path = config_path
        self.config = self._load_config(config_path)
        self.state_path = Path(self.config["state_file"])
        self.cache_path = Path(self.config["research_cache_file"])
        self.output_dir = Path(self.config["output_dir"])
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.state = self._load_state()

    @staticmethod
    def _load_config(path: Path) -> dict[str, Any]:
        if not path.exists():
            path.write_text(json.dumps(DEFAULT_CONFIG, indent=2), encoding="utf-8")
            return DEFAULT_CONFIG
        with path.open("r", encoding="utf-8") as f:
            user_cfg = json.load(f)
        cfg = json.loads(json.dumps(DEFAULT_CONFIG))
        merge_dict(cfg, user_cfg)
        return cfg

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {
                "last_research_ts": None,
                "last_report_ts": None,
                "seen_ids": [],
            }
        with self.state_path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def _save_state(self) -> None:
        seen_limit = 5000
        if len(self.state["seen_ids"]) > seen_limit:
            self.state["seen_ids"] = self.state["seen_ids"][-seen_limit:]
        with self.state_path.open("w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2)

    def run_once(self, force_report: bool = False) -> str | None:
        now = now_utc()
        new_signals = self.run_research(now)
        should_report, report_type = self.should_generate_report(now, new_signals, force_report)
        report_path = None
        if should_report:
            report_path = self.generate_report(now, report_type)
            self.state["last_report_ts"] = now.isoformat()
        self.state["last_research_ts"] = now.isoformat()
        self._save_state()
        return report_path

    def run_research(self, now: dt.datetime) -> list[Signal]:
        rss_urls = self.config["sources"].get("rss", [])
        seen = set(self.state.get("seen_ids", []))
        collected: list[Signal] = []

        for url in rss_urls:
            for item in fetch_rss(url):
                stable = stable_id(item.get("id") or item.get("link") or item.get("title", ""))
                if stable in seen:
                    continue
                signal = self._build_signal(item, url, stable)
                if signal.relevance_score > 0:
                    collected.append(signal)
                    seen.add(stable)

        if collected:
            with self.cache_path.open("a", encoding="utf-8") as f:
                for s in collected:
                    f.write(json.dumps(s.__dict__, ensure_ascii=False) + "\n")

        self.state["seen_ids"] = list(seen)
        return collected

    def _build_signal(self, item: dict[str, str], source: str, stable: str) -> Signal:
        title = clean_text(item.get("title", "").strip())
        summary = clean_text(item.get("summary", "").strip())
        link = item.get("link", "")
        published = item.get("published", "")
        text = f"{title} {summary}".lower()

        topics: list[str] = []
        score = 0
        rationale: list[str] = []
        for topic, keywords in self.config["topic_rules"].items():
            hits = sum(1 for kw in keywords if kw.lower() in text)
            if hits:
                topics.append(topic)
                score += min(30, hits * 8)
                rationale.append(f"{topic}: {hits} keyword hit(s)")

        dim_scores = self._dimension_scores(text)
        score += min(30, sum(dim_scores.values()) // 2)
        for dim, val in sorted(dim_scores.items(), key=lambda x: x[1], reverse=True):
            if val > 0:
                rationale.append(f"{dim} score {val}")

        org = self.config["organization"].lower()
        role_terms = [t for t in re.split(r"[^a-zA-Z0-9]+", self.config["role"].lower()) if t]
        if org in text:
            score += 25
            rationale.append("organization mention")
        score += min(20, sum(1 for t in role_terms if len(t) > 3 and t in text) * 3)

        urgency = score
        critical_hits = sum(1 for kw in self.config["critical_keywords"] if kw.lower() in text)
        urgency += critical_hits * 12

        return Signal(
            id=stable,
            title=title,
            link=link,
            published=published,
            source=source,
            summary=short_summary(summary),
            topics=topics or ["General Technology Signals"],
            relevance_score=min(score, 100),
            urgency_score=min(urgency, 100),
            dimension_scores=dim_scores,
            rationale=rationale[:6],
        )

    def _dimension_scores(self, text: str) -> dict[str, int]:
        dims = self.config.get("relevance_dimensions", {})
        scores: dict[str, int] = {}
        for dim_name, conf in dims.items():
            keywords = conf.get("keywords", [])
            weight = float(conf.get("weight", 1.0))
            hits = sum(1 for kw in keywords if kw.lower() in text)
            scores[dim_name] = min(20, int(hits * 5 * weight))
        return scores

    def should_generate_report(self, now: dt.datetime, signals: list[Signal], force_report: bool) -> tuple[bool, str]:
        if force_report:
            return True, "regular"

        urgent_threshold = int(self.config["urgent_threshold"])
        if any(s.urgency_score >= urgent_threshold for s in signals):
            return True, "urgent"

        last_report = parse_ts(self.state.get("last_report_ts"))
        if last_report is None:
            return True, "regular"

        elapsed_h = (now - last_report).total_seconds() / 3600
        if elapsed_h >= float(self.config["report_interval_hours"]):
            return True, "regular"

        return False, "regular"

    def generate_report(self, now: dt.datetime, report_type: str) -> str:
        all_signals = self._load_recent_signals(now)
        grouped = group_by_topics(all_signals)

        top_operational = sorted(all_signals, key=lambda x: x.urgency_score, reverse=True)[:5]
        top_tactical = sorted(all_signals, key=lambda x: x.relevance_score, reverse=True)[:8]
        top_strategic = strategic_candidates(all_signals)[:8]

        kpi_rows = build_kpi_rows(self.config, all_signals, now)
        changed_summary = summarize_change_windows(all_signals, now)
        recommendations = build_recommendations(top_operational, top_tactical)

        content = []
        content.append(f"# {self.config['organization']} Tech Intelligence Report ({report_type.title()})")
        content.append("")
        content.append(f"- Generated: {now.isoformat()} UTC")
        content.append(f"- Role focus: {self.config['role']}")
        content.append(f"- Coverage window: last {self.config['lookback_days_for_report']} days")
        content.append("")

        content.append("## Priority Signals by Timeframe")
        content.append("")
        content.append("## What changed since last report window")
        content.append("")
        content.append(f"- Signals in last 7 days: **{changed_summary['recent']}**")
        content.append(f"- Signals in prior 7 days: **{changed_summary['previous']}**")
        content.append(f"- Net change: **{changed_summary['delta']:+}**")
        content.append("")
        content.append("### Operational (imminent: 0-30 days)")
        if not top_operational:
            content.append("- No high-urgency external signals were captured in the current window.")
        for s in top_operational:
            content.append(
                f"- **{s.title}** (urgency {s.urgency_score}/100): {s.summary} [{s.link}]({s.link}) "
                f"(why: {', '.join(s.rationale[:2]) or 'scored by relevance model'})"
            )
        content.append("")
        content.append("### Tactical (30-180 days)")
        if not top_tactical:
            content.append("- No tactical-priority items were captured in this cycle.")
        for s in top_tactical:
            content.append(f"- **{s.title}** (relevance {s.relevance_score}/100): {s.summary} [{s.link}]({s.link})")
        content.append("")
        content.append("### Strategic (6-24 months)")
        if not top_strategic:
            content.append("- No strategic horizon indicators were captured in this cycle.")
        for s in top_strategic:
            content.append(f"- **{s.title}**: {s.summary} [{s.link}]({s.link})")
        content.append("")
        content.append("## Recommended decisions this week")
        content.append("")
        for rec in recommendations:
            content.append(f"- **{rec['decision']}** — {rec['reason']} (owner: {rec['owner']}, horizon: {rec['horizon']})")

        content.append("")
        content.append("## Topic Summaries")
        content.append("")

        max_items = int(self.config["max_items_per_topic"])
        if not grouped:
            content.append(
                "- No qualifying external signals were captured for this interval. "
                "Consider adding sources or lowering threshold strictness."
            )
        else:
            for topic, items in grouped.items():
                content.append(f"### {topic}")
                for s in sorted(items, key=lambda x: (x.relevance_score, x.urgency_score), reverse=True)[:max_items]:
                    top_dims = ", ".join(
                        f"{k}:{v}" for k, v in sorted(s.dimension_scores.items(), key=lambda x: x[1], reverse=True)[:2] if v > 0
                    ) or "n/a"
                    content.append(
                        f"- **{s.title}** — {s.summary}  \\\n  Source: {domain_of(s.source)} | Published: {s.published or 'n/a'} | "
                        f"Scores: R{s.relevance_score}/U{s.urgency_score} | Dims: {top_dims} | Link: {s.link}"
                    )
                content.append("")

        content.append("## Quantifiable Key Indicators")
        content.append("")
        content.append("| Indicator | Value | Change | Notes |")
        content.append("|---|---:|---:|---|")
        for row in kpi_rows:
            content.append(f"| {row['indicator']} | {row['value']} | {row['change']} | {row['notes']} |")

        ts = now.strftime("%Y%m%d")
        suffix = "urgent" if report_type == "urgent" else "regular"
        out_path = self.output_dir / f"{suffix}_report_{ts}.md"
        out_path.write_text("\n".join(content) + "\n", encoding="utf-8")
        self._write_report_json(
            now=now,
            report_type=report_type,
            path_base=self.output_dir / f"{suffix}_report_{ts}",
            grouped=grouped,
            top_operational=top_operational,
            top_tactical=top_tactical,
            top_strategic=top_strategic,
            recommendations=recommendations,
            kpi_rows=kpi_rows,
            changed_summary=changed_summary,
        )
        return str(out_path)

    def _write_report_json(
        self,
        now: dt.datetime,
        report_type: str,
        path_base: Path,
        grouped: dict[str, list[Signal]],
        top_operational: list[Signal],
        top_tactical: list[Signal],
        top_strategic: list[Signal],
        recommendations: list[dict[str, str]],
        kpi_rows: list[dict[str, str]],
        changed_summary: dict[str, int],
    ) -> None:
        payload = {
            "organization": self.config["organization"],
            "role": self.config["role"],
            "generated_utc": now.isoformat(),
            "report_type": report_type,
            "changed_summary": changed_summary,
            "priority": {
                "operational": [signal_to_dict(s) for s in top_operational],
                "tactical": [signal_to_dict(s) for s in top_tactical],
                "strategic": [signal_to_dict(s) for s in top_strategic],
            },
            "recommendations": recommendations,
            "topics": {topic: [signal_to_dict(s) for s in items] for topic, items in grouped.items()},
            "kpis": kpi_rows,
        }
        path_base.with_suffix(".json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _load_recent_signals(self, now: dt.datetime) -> list[Signal]:
        if not self.cache_path.exists():
            return []
        lookback = dt.timedelta(days=int(self.config["lookback_days_for_report"]))
        cutoff = now - lookback
        signals: list[Signal] = []
        with self.cache_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                raw = json.loads(line)
                published = parse_rss_date(raw.get("published", ""))
                if published and published < cutoff:
                    continue
                signals.append(Signal.from_dict(raw))
        return signals


def fetch_rss(url: str) -> list[dict[str, str]]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as response:
            raw = response.read()
    except Exception:
        return []

    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []

    items: list[dict[str, str]] = []
    for item in root.findall(".//item") + root.findall(".//{http://www.w3.org/2005/Atom}entry"):
        title = text_of(item, ["title", "{http://www.w3.org/2005/Atom}title"])
        link = extract_link(item)
        summary = text_of(item, ["description", "summary", "{http://www.w3.org/2005/Atom}summary"])
        pub = text_of(
            item,
            [
                "pubDate",
                "published",
                "updated",
                "{http://www.w3.org/2005/Atom}updated",
                "{http://www.w3.org/2005/Atom}published",
            ],
        )
        guid = text_of(item, ["guid", "id", "{http://www.w3.org/2005/Atom}id"])
        if title and link:
            items.append({"title": title, "link": link, "summary": summary, "published": pub, "id": guid})
    return items


def extract_link(item: ET.Element) -> str:
    direct = text_of(item, ["link", "{http://www.w3.org/2005/Atom}link"])
    if direct and direct.startswith("http"):
        return direct
    for tag in ["link", "{http://www.w3.org/2005/Atom}link"]:
        el = item.find(tag)
        if el is not None:
            href = el.attrib.get("href")
            if href:
                return href
    return ""


def text_of(item: ET.Element, tags: list[str]) -> str:
    for tag in tags:
        el = item.find(tag)
        if el is not None and el.text:
            return clean_text(el.text)
    return ""


def clean_text(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def short_summary(summary: str) -> str:
    if not summary:
        return "No summary provided by source."
    return textwrap.shorten(summary, width=220, placeholder="...")


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def parse_ts(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value)
    except ValueError:
        return None


def parse_rss_date(value: str) -> dt.datetime | None:
    if not value:
        return None
    for fmt in [
        "%a, %d %b %Y %H:%M:%S %z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
    ]:
        try:
            parsed = dt.datetime.strptime(value, fmt)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=dt.timezone.utc)
            return parsed.astimezone(dt.timezone.utc)
        except ValueError:
            continue
    return None


def stable_id(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8", errors="ignore")).hexdigest()[:24]


def merge_dict(base: dict[str, Any], patch: dict[str, Any]) -> None:
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            merge_dict(base[k], v)
        else:
            base[k] = v


def group_by_topics(signals: list[Signal]) -> dict[str, list[Signal]]:
    grouped: dict[str, list[Signal]] = {}
    for s in signals:
        for t in s.topics:
            grouped.setdefault(t, []).append(s)
    return dict(sorted(grouped.items(), key=lambda kv: kv[0]))


def strategic_candidates(signals: list[Signal]) -> list[Signal]:
    keywords = {"governance", "infrastructure", "workforce", "regulation", "investment", "productivity"}
    scored = []
    for s in signals:
        body = f"{s.title} {s.summary}".lower()
        long_term_hit = sum(1 for kw in keywords if kw in body)
        scored.append((long_term_hit, s.relevance_score, s))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [x[2] for x in scored if x[0] > 0]


def domain_of(url: str) -> str:
    if not url:
        return "n/a"
    return urllib.parse.urlparse(url).netloc


def signal_to_dict(signal: Signal) -> dict[str, Any]:
    return {
        "id": signal.id,
        "title": signal.title,
        "link": signal.link,
        "published": signal.published,
        "source": signal.source,
        "summary": signal.summary,
        "topics": signal.topics,
        "relevance_score": signal.relevance_score,
        "urgency_score": signal.urgency_score,
        "dimension_scores": signal.dimension_scores,
        "rationale": signal.rationale,
    }


def fetch_stock_price_stooq(symbol: str) -> tuple[str, str]:
    url = f"https://stooq.com/q/l/?s={urllib.parse.quote(symbol.lower())}&f=sd2t2ohlcv&h&e=csv"
    try:
        with urllib.request.urlopen(url, timeout=20) as response:
            rows = response.read().decode("utf-8", errors="replace").strip().splitlines()
        if len(rows) < 2:
            return "n/a", "n/a"
        cols = rows[1].split(",")
        close = cols[6]
        open_p = cols[4]
        if close == "N/D" or open_p == "N/D":
            return "n/a", "n/a"
        close_v = float(close)
        open_v = float(open_p)
        change_pct = ((close_v - open_v) / open_v * 100) if open_v else 0.0
        return f"{close_v:.2f}", f"{change_pct:+.2f}%"
    except Exception:
        return "n/a", "n/a"


def build_kpi_rows(config: dict[str, Any], signals: list[Signal], now: dt.datetime) -> list[dict[str, str]]:
    rows = []
    for ticker in config.get("kpi", {}).get("stocks", []):
        value, change = fetch_stock_price_stooq(ticker)
        rows.append(
            {
                "indicator": f"Stock: {ticker}",
                "value": value,
                "change": change,
                "notes": "Daily close and open-change (Stooq)",
            }
        )
    windows = summarize_change_windows(signals, now)
    rows.extend(
        [
            {
                "indicator": "Signal volume (7d)",
                "value": str(windows["recent"]),
                "change": f"{windows['delta']:+}",
                "notes": "Count of captured relevant signals in last 7 days",
            },
            {
                "indicator": "Urgent signals (7d)",
                "value": str(count_urgent(signals, now, 7)),
                "change": "n/a",
                "notes": "Signals with urgency >= configured urgent threshold",
            },
            {
                "indicator": "Talent/workforce signals (7d)",
                "value": str(count_topic(signals, now, 7, "Talent & Workforce")),
                "change": "n/a",
                "notes": "Signals classified under workforce/talent",
            },
            {
                "indicator": "AEC industry signals (7d)",
                "value": str(count_topic(signals, now, 7, "AECOM / AEC Industry")),
                "change": "n/a",
                "notes": "Signals classified under AEC industry impact",
            },
        ]
    )
    return rows


def signal_time(signal: Signal) -> dt.datetime | None:
    return parse_rss_date(signal.published)


def summarize_change_windows(signals: list[Signal], now: dt.datetime) -> dict[str, int]:
    recent_start = now - dt.timedelta(days=7)
    previous_start = now - dt.timedelta(days=14)
    recent = 0
    previous = 0
    for s in signals:
        ts = signal_time(s)
        if ts is None:
            continue
        if recent_start <= ts <= now:
            recent += 1
        elif previous_start <= ts < recent_start:
            previous += 1
    return {"recent": recent, "previous": previous, "delta": recent - previous}


def count_urgent(signals: list[Signal], now: dt.datetime, days: int) -> int:
    cutoff = now - dt.timedelta(days=days)
    return sum(1 for s in signals if s.urgency_score >= 80 and (signal_time(s) or now) >= cutoff)


def count_topic(signals: list[Signal], now: dt.datetime, days: int, topic: str) -> int:
    cutoff = now - dt.timedelta(days=days)
    return sum(1 for s in signals if topic in s.topics and (signal_time(s) or now) >= cutoff)


def build_recommendations(top_operational: list[Signal], top_tactical: list[Signal]) -> list[dict[str, str]]:
    recommendations: list[dict[str, str]] = []
    if top_operational:
        highest = top_operational[0]
        recommendations.append(
            {
                "decision": "Review immediate operational exposure",
                "reason": f"Top urgent signal: {highest.title[:90]}",
                "owner": "Ops AI leadership",
                "horizon": "0-30 days",
            }
        )
    if top_tactical:
        recommendations.append(
            {
                "decision": "Prioritize one automation initiative for next quarter",
                "reason": f"{len(top_tactical)} tactical signals suggest process/tech shifts",
                "owner": "Delivery team leads",
                "horizon": "30-180 days",
            }
        )
    if not recommendations:
        recommendations.append(
            {
                "decision": "Expand monitoring coverage",
                "reason": "No strong external signals captured; increase source breadth and validate feed access",
                "owner": "Research ops",
                "horizon": "This week",
            }
        )
    recommendations.append(
        {
            "decision": "Validate talent plan alignment",
            "reason": "Track whether workforce-related signals are increasing versus prior week",
            "owner": "People + AI leadership",
            "horizon": "This month",
        }
    )
    return recommendations[:5]


def run_loop(agent: ResearchAgent) -> None:
    print("Starting research/report loop. Ctrl+C to stop.")
    while True:
        now = now_utc()
        last_research = parse_ts(agent.state.get("last_research_ts"))
        should_research = (
            last_research is None
            or (now - last_research).total_seconds() / 3600 >= float(agent.config["research_interval_hours"])
        )

        if should_research:
            report = agent.run_once(force_report=False)
            if report:
                print(f"Report written: {report}")
            else:
                print("Research run complete; no report this cycle.")
        else:
            print("Waiting until next research interval...")

        time.sleep(300)


def main() -> None:
    parser = argparse.ArgumentParser(description="AECOM-focused research and summarizer agent")
    parser.add_argument("--config", default="config.json", help="Path to JSON configuration file")
    parser.add_argument("--run-once", action="store_true", help="Run research/report once and exit")
    parser.add_argument("--force-report", action="store_true", help="Generate regular report immediately")
    args = parser.parse_args()

    agent = ResearchAgent(Path(args.config))

    if args.run_once:
        path = agent.run_once(force_report=args.force_report)
        if path:
            print(path)
        else:
            print("No report generated.")
    else:
        run_loop(agent)


if __name__ == "__main__":
    main()
