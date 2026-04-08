#!/usr/bin/env python3
"""Simple local dashboard for research_agent reports.

Usage:
  python3 dashboard.py --reports-dir reports --port 8765
  python3 dashboard.py --reports-dir reports --export-html dashboard.html
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any


def load_latest_report_json(reports_dir: Path) -> dict[str, Any] | None:
    files = sorted(reports_dir.glob("*_report_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return None
    return json.loads(files[0].read_text(encoding="utf-8"))


def render_dashboard_html(data: dict[str, Any] | None) -> str:
    if not data:
        return """<html><body><h1>No report data found</h1><p>Run research_agent.py first.</p></body></html>"""

    def cards(rows: list[dict[str, str]]) -> str:
        out = []
        for r in rows:
            out.append(
                f"<div class='card'><div class='title'>{r['indicator']}</div>"
                f"<div class='value'>{r['value']}</div><div class='chg'>{r['change']}</div>"
                f"<div class='note'>{r['notes']}</div></div>"
            )
        return "".join(out)

    def bullet(signals: list[dict[str, Any]], score_name: str) -> str:
        if not signals:
            return "<li>No items.</li>"
        return "".join(
            f"<li><b>{s.get('impact_headline') or s.get('title','(untitled)')}</b> "
            f"<span class='pill'>{score_name}: {s.get(score_name, 0)}</span><br>"
            f"<small>{s.get('summary','')}</small><br>"
            f"<a href='{s.get('link','')}' target='_blank'>Read source article</a></li>"
            for s in signals
        )

    topics_html = ""
    topics = data.get("topics", {})
    if topics:
        for topic, items in topics.items():
            topic_items = "".join(
                f"<li><a href='{i.get('link','')}' target='_blank'>{i.get('title','(untitled)')}</a>"
                f" <small>(R{i.get('relevance_score',0)}/U{i.get('urgency_score',0)})</small></li>"
                for i in items[:8]
            )
            topics_html += f"<details><summary>{topic} ({len(items)})</summary><ul>{topic_items}</ul></details>"
    else:
        topics_html = "<p>No topic signals available in latest report.</p>"

    recs = data.get("recommendations", [])
    rec_html = "".join(
        f"<li><b>{r.get('decision','')}</b> — {r.get('reason','')} "
        f"<small>[owner: {r.get('owner','n/a')} | horizon: {r.get('horizon','n/a')}]</small></li>"
        for r in recs
    ) or "<li>No recommendations generated.</li>"

    chg = data.get("changed_summary", {"recent": 0, "previous": 0, "delta": 0})

    return f"""
<!doctype html>
<html>
<head>
<meta charset='utf-8'>
<title>AECOM Intelligence Dashboard</title>
<style>
body {{ font-family: Arial, sans-serif; margin: 24px; background: #f7f8fa; color: #1d2433; }}
h1, h2, h3 {{ margin: 0.3rem 0; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fit,minmax(220px,1fr)); gap: 12px; }}
.card {{ background: white; border-radius: 10px; padding: 12px; border: 1px solid #e3e7ef; }}
.title {{ font-weight: 600; font-size: 0.9rem; }}
.value {{ font-size: 1.4rem; margin-top: 6px; }}
.chg {{ color: #2a6; font-weight: 700; }}
.note {{ color: #667; font-size: 0.8rem; margin-top: 6px; }}
.cols {{ display: grid; grid-template-columns: repeat(auto-fit,minmax(320px,1fr)); gap: 12px; }}
.panel {{ background: white; border:1px solid #e3e7ef; border-radius: 10px; padding: 14px; }}
.pill {{ background:#eef3ff; border-radius:999px; padding:2px 8px; font-size:0.75rem; }}
small {{ color:#667; }}
a {{ color:#1144aa; text-decoration:none; }}
a:hover {{ text-decoration:underline; }}
</style>
</head>
<body>
  <h1>{data.get('organization','Org')} Tech Intelligence Dashboard</h1>
  <p><b>Generated:</b> {data.get('generated_utc','n/a')} UTC | <b>Type:</b> {data.get('report_type','n/a').title()} | <b>Role:</b> {data.get('role','n/a')}</p>

  <div class='panel'>
    <h3>What changed (7d)</h3>
    <p>Recent: <b>{chg.get('recent',0)}</b> | Prior: <b>{chg.get('previous',0)}</b> | Delta: <b>{chg.get('delta',0):+}</b></p>
  </div>

  <h2>KPI Snapshot</h2>
  <div class='grid'>{cards(data.get('kpis', []))}</div>

  <h2>Priority Signals</h2>
  <div class='cols'>
    <div class='panel'><h3>Operational</h3><ul>{bullet(data.get('priority', {}).get('operational', []), 'urgency_score')}</ul></div>
    <div class='panel'><h3>Tactical</h3><ul>{bullet(data.get('priority', {}).get('tactical', []), 'relevance_score')}</ul></div>
    <div class='panel'><h3>Strategic</h3><ul>{bullet(data.get('priority', {}).get('strategic', []), 'relevance_score')}</ul></div>
  </div>

  <h2>Recommended Decisions</h2>
  <div class='panel'><ul>{rec_html}</ul></div>

  <h2>Topics</h2>
  <div class='panel'>{topics_html}</div>
</body>
</html>
"""


class DashboardHandler(BaseHTTPRequestHandler):
    reports_dir: Path = Path("reports")

    def do_GET(self) -> None:  # noqa: N802
        data = load_latest_report_json(self.reports_dir)
        body = render_dashboard_html(data).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run_server(reports_dir: Path, port: int) -> None:
    DashboardHandler.reports_dir = reports_dir
    server = HTTPServer(("127.0.0.1", port), DashboardHandler)
    print(f"Dashboard: http://127.0.0.1:{port}")
    server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="Local dashboard for research_agent reports")
    parser.add_argument("--reports-dir", default="reports", help="Path to directory containing report JSON files")
    parser.add_argument("--port", default=8765, type=int, help="HTTP port for dashboard")
    parser.add_argument("--export-html", default="", help="Write dashboard HTML to this file and exit")
    args = parser.parse_args()

    reports_dir = Path(args.reports_dir)
    data = load_latest_report_json(reports_dir)
    html = render_dashboard_html(data)

    if args.export_html:
        out = Path(args.export_html)
        out.write_text(html, encoding="utf-8")
        print(f"Wrote dashboard HTML to {out.resolve()}")
        return

    run_server(reports_dir, args.port)


if __name__ == "__main__":
    main()
