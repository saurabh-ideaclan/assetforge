"""Self-contained HTML rendering of a usage/cost report (no external assets, light and dark themes)."""

from __future__ import annotations

import html
import math
from pathlib import Path
from typing import Any

# Colour tokens are shared with the desktop app so reports and the app look the same.
TOKENS = (Path(__file__).resolve().parent.parent / "app" / "static" / "tokens.css").read_text()

STYLE = """
body{margin:0;background:var(--surface-0)}
.viz-root{font:14px/1.45 system-ui,-apple-system,Segoe UI,sans-serif;color:var(--text-primary);background:var(--surface-0);
padding:24px 16px;max-width:1080px;margin:0 auto}
h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:28px 0 10px}.sub{color:var(--text-secondary);margin:0 0 18px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.tile{background:var(--surface-1);border:1px solid var(--border);border-radius:10px;padding:12px 14px}
.tile .label{color:var(--text-secondary);font-size:12px}.tile .value{font-size:24px;font-weight:600;margin-top:2px}
.tile .note{color:var(--text-muted);font-size:12px}
.card{background:var(--surface-1);border:1px solid var(--border);border-radius:10px;padding:14px;position:relative}
svg text{fill:var(--text-muted);font-size:11px}.grid line{stroke:var(--grid)}.axis{stroke:var(--axis)}
.bar{fill:var(--series-1)}.hit:hover+.bar,.bar.active{fill:var(--hover)}
.tip{position:absolute;pointer-events:none;background:var(--surface-1);border:1px solid var(--border);border-radius:8px;
padding:6px 9px;font-size:12px;box-shadow:0 4px 14px rgba(0,0,0,.12);display:none;white-space:nowrap}
table{width:100%;border-collapse:collapse;background:var(--surface-1);border:1px solid var(--border);border-radius:10px;
overflow:hidden;font-size:13px}th,td{padding:7px 10px;text-align:right;border-bottom:1px solid var(--border)}
th{color:var(--text-secondary);font-weight:600;background:var(--surface-0)}th:first-child,td:first-child{text-align:left}
td.text{text-align:left;max-width:360px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.wrap{overflow-x:auto}details summary{cursor:pointer;color:var(--text-secondary);margin-top:8px}
.muted{color:var(--text-muted)}
"""

SCRIPT = """
const tip=document.getElementById('tip');const card=document.getElementById('chart-card');
document.querySelectorAll('.hit').forEach(h=>{
 h.addEventListener('mousemove',e=>{const r=card.getBoundingClientRect();tip.style.display='block';
  tip.innerHTML=h.dataset.tip;let x=e.clientX-r.left+12;if(x+tip.offsetWidth>r.width)x=e.clientX-r.left-tip.offsetWidth-12;
  tip.style.left=x+'px';tip.style.top=(e.clientY-r.top-10)+'px';h.nextElementSibling.classList.add('active')});
 h.addEventListener('mouseleave',()=>{tip.style.display='none';h.nextElementSibling.classList.remove('active')})});
"""


def _money(v: float | None) -> str:
    if v is None:
        return "–"
    return f"${v:,.4f}" if 0 < abs(v) < 1 else f"${v:,.2f}"


def _int(v: int | None) -> str:
    return "–" if v is None else f"{v:,}"


def _nice_max(value: float) -> float:
    if value <= 0:
        return 1.0
    exponent = math.floor(math.log10(value))
    for step in (1, 2, 2.5, 5, 10):
        top = step * 10**exponent
        if top >= value:
            return top
    return 10 ** (exponent + 1)


def _bar_path(x: float, y: float, w: float, h: float, r: float = 4.0) -> str:
    r = min(r, w / 2, h)
    return (f"M{x:.1f},{y + h:.1f} V{y + r:.1f} Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f} H{x + w - r:.1f} "
            f"Q{x + w:.1f},{y:.1f} {x + w:.1f},{y + r:.1f} V{y + h:.1f} Z")  # fmt: skip


def chart(series: list[dict[str, Any]]) -> str:
    width, height, left, right, top, bottom = 760, 240, 56, 12, 12, 28
    plot_w, plot_h = width - left - right, height - top - bottom
    peak = _nice_max(max((p["cost_usd"] for p in series), default=0))
    band = plot_w / max(1, len(series))
    bar_w = max(2.0, min(24.0, band * 0.6))
    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" aria-label="Cost per period">']
    parts.append('<g class="grid">')
    for i in range(5):
        value = peak * i / 4
        y = top + plot_h - plot_h * i / 4
        parts.append(f'<line x1="{left}" x2="{width - right}" y1="{y:.1f}" y2="{y:.1f}"/>')
        parts.append(f'<text x="{left - 6}" y="{y + 4:.1f}" text-anchor="end">{_money(value)}</text>')
    parts.append("</g>")
    label_every = max(1, math.ceil(len(series) / 12))
    for i, point in enumerate(series):
        x = left + band * i + (band - bar_w) / 2
        h = plot_h * point["cost_usd"] / peak if peak else 0
        tip = (f"<b>{html.escape(point['period'])}</b><br>{_money(point['cost_usd'])} · {point['images']} image(s)"
               f" · {point['calls']} call(s)<br>{_int(point['total_tokens'])} tokens")  # fmt: skip
        parts.append(f'<rect class="hit" x="{left + band * i:.1f}" y="{top}" width="{band:.1f}" height="{plot_h}" '
                     f'fill="transparent" data-tip="{html.escape(tip)}"/>')  # fmt: skip
        if h > 0:
            parts.append(f'<path class="bar" d="{_bar_path(x, top + plot_h - h, bar_w, h)}"/>')
        else:
            parts.append(f'<path class="bar" d="M{x:.1f},{top + plot_h} h0"/>')
        if i % label_every == 0:
            label = point["period"][5:] if len(point["period"]) == 10 else point["period"]
            parts.append(f'<text x="{x + bar_w / 2:.1f}" y="{height - 8}" text-anchor="middle">{html.escape(label)}</text>')
    parts.append(f'<line class="axis" x1="{left}" x2="{width - right}" y1="{top + plot_h}" y2="{top + plot_h}"/>')
    parts.append("</svg>")
    return "".join(parts)


def _table(rows: list[dict[str, Any]], key_label: str) -> str:
    if not rows:
        return '<p class="muted">No usage in this range.</p>'
    head = (f"<tr><th>{key_label}</th><th>Calls</th><th>Images</th><th>Input tokens</th><th>Output tokens</th>"
            "<th>Cost</th><th>Share</th><th>$/image</th></tr>")  # fmt: skip
    body = "".join(
        f"<tr><td>{html.escape(str(r['key']))}</td><td>{_int(r['calls'])}</td><td>{_int(r['images'])}</td>"
        f"<td>{_int(r['input_text_tokens'] + r['input_image_tokens'])}</td>"
        f"<td>{_int(r['output_image_tokens'] + r['output_text_tokens'])}</td><td>{_money(r['cost_usd'])}</td>"
        f"<td>{r.get('share', 0) * 100:.1f}%</td><td>{_money(r['avg_cost_per_image'])}</td></tr>"
        for r in rows
    )
    return f'<div class="wrap"><table>{head}{body}</table></div>'


def render(report: dict[str, Any], *, title: str = "AssetForge usage & cost report") -> str:
    t = report["totals"]
    rng = report["range"]
    tiles = [
        ("Total cost", _money(t["cost_usd"]), f"est. {_money(t['estimated_cost_usd'])} before calls"),
        ("Images", _int(t["images"]), f"{_int(t['calls'])} provider calls"),
        ("Tokens", _int(t["total_tokens"]), f"{_int(t['output_image_tokens'])} image-output"),
        ("Avg $/image", _money(t["avg_cost_per_image"]), "actual"),
        ("Estimate accuracy", "–" if t["estimate_error_pct"] is None else f"{t['estimate_error_pct']:+.1f}%",
         "estimate vs actual"),
    ]  # fmt: skip
    tiles_html = "".join(f'<div class="tile"><div class="label">{a}</div><div class="value">{b}</div>'
                         f'<div class="note">{c}</div></div>' for a, b, c in tiles)  # fmt: skip
    series_rows = "".join(
        f"<tr><td>{html.escape(p['period'])}</td><td>{_int(p['calls'])}</td><td>{_int(p['images'])}</td>"
        f"<td>{_int(p['input_text_tokens'])}</td><td>{_int(p['input_image_tokens'])}</td>"
        f"<td>{_int(p['output_image_tokens'])}</td><td>{_int(p['output_text_tokens'])}</td><td>{_money(p['cost_usd'])}</td></tr>"
        for p in report["series"]
    )  # fmt: skip
    series_table = ("<table><tr><th>Period</th><th>Calls</th><th>Images</th><th>Text in</th><th>Image in</th>"
                    f"<th>Image out</th><th>Text out</th><th>Cost</th></tr>{series_rows}</table>")  # fmt: skip
    jobs_rows = "".join(
        f"<tr><td>{html.escape(j['job_id'] or '')}</td><td>{html.escape(j['created_at'])}</td>"
        f"<td>{html.escape(j['provider'])}/{html.escape(j['model'])}</td><td>{_int(j['images'])}</td>"
        f"<td>{_int(j['total_tokens'])}</td><td>{_money(j['cost_usd'])}</td>"
        f"<td class='text' title='{html.escape(j['prompt'] or '')}'>{html.escape(j['prompt'] or '')}</td></tr>"
        for j in report["top_jobs"]
    )  # fmt: skip
    jobs_table = ("<table><tr><th>Job</th><th>When (UTC)</th><th>Model</th><th>Images</th><th>Tokens</th><th>Cost</th>"
                  f"<th>Prompt</th></tr>{jobs_rows}</table>") if jobs_rows else '<p class="muted">None.</p>'  # fmt: skip
    rates_rows = "".join(
        f"<tr><td>{html.escape(m)}</td>" + "".join(
            f"<td>{(info or {}).get('rates', {}).get(f, '–')}</td>"
            for f in ("input_text", "input_image", "output_image", "output_text", "per_image"))
        + f"<td class='text'>{html.escape((info or {}).get('source', ''))}</td></tr>"
        for m, info in report["pricing"]["rates"].items()
    )  # fmt: skip
    pricing = report["pricing"]
    fetched = (f"fetched {html.escape(pricing['fetched_at'])} ({pricing['age_days']} days ago)"
               if pricing["fetched_at"] else "built-in defaults (never fetched)")  # fmt: skip
    b = report["budget"]
    budget_line = " · ".join(
        f"{p}: {_money(b[p]['spent_usd'])} spent" + (f" of {_money(b[p]['limit_usd'])}" if b[p]["limit_usd"] else "")
        for p in ("daily", "monthly")
    )
    activity = ", ".join(f"{k}: {sum(v.values())}" for k, v in report["activity"].items()) or "none"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title>
<style>{TOKENS}{STYLE}</style></head><body><div class="viz-root">
<h1>{html.escape(title)}</h1>
<p class="sub">{html.escape(rng['label'])} · {html.escape(rng['from'] or 'beginning')} → {html.escape(rng['to'])}
({html.escape(rng['tz'])}) · prices: {fetched}</p>
<div class="tiles">{tiles_html}</div>
<h2>Cost per {html.escape(rng['interval'])}</h2>
<div class="card" id="chart-card">{chart(report['series'])}<div class="tip" id="tip"></div>
<details><summary>Show as table</summary><div class="wrap">{series_table}</div></details></div>
<h2>By provider</h2>{_table(report['by_provider'], 'Provider')}
<h2>By model</h2>{_table(report['by_model'], 'Model')}
<h2>By project</h2>{_table(report['by_project'], 'Project')}
<h2>Most expensive calls</h2><div class="wrap">{jobs_table}</div>
<h2>Prices used (USD per 1M tokens; per image)</h2>
<div class="wrap"><table><tr><th>Model</th><th>Text in</th><th>Image in</th><th>Image out</th><th>Text out</th>
<th>Per image</th><th>Source</th></tr>{rates_rows}</table></div>
<p class="muted">Source: {html.escape(pricing['source_url'])}. Budget now: {budget_line}. Jobs in range: {html.escape(activity)}.</p>
</div><script>{SCRIPT}</script></body></html>"""
