"""Hourly board scan: store campaigns, alert on new ones and on keyword matches."""
from __future__ import annotations

import re
import time

from rich.console import Console

from app import db
from app.notify import telegram
from app.scanner import board

console = Console()

# GTA VI watch list (PLAN.md section 7). Word boundaries stop "Jason" matching "Jasonville" etc.
KEYWORDS = re.compile(r"\b(GTA|Grand Theft|Rockstar|Vice City|Leonida|Lucia|Jason)\b", re.I)
HIGH_RATE = 2.0          # dollars per 1,000 views that counts as "high-paying"
DETAIL_DELAY_S = 3       # pause between campaign page fetches
MAX_DETAILS_PER_SCAN = 8


def keyword_hits(campaign: dict) -> list[str]:
    text = f"{campaign.get('title', '')}\n{campaign.get('description', '')}"
    return sorted({m.group(1).lower() for m in KEYWORDS.finditer(text)})


def _format(c: dict, detail: dict | None, tags: list[str]) -> str:
    lines = [
        f"{' / '.join(tags)}: {c.get('title')}",
        f"Brand: {c.get('brand')} | {c.get('category')} | {', '.join(c.get('platforms') or [])}",
        f"Rate: ${c.get('payoutSortRaw')}/1k | budget left ${c.get('availableBudgetRaw', 0):,.0f}"
        f" of ${c.get('budgetTotalRaw', 0):,.0f}",
        f"Application needed: {'yes' if c.get('requiresApplication') else 'no'}",
    ]
    if c.get("type") == "pool":
        lines[2] = (f"Pool campaign: ${c.get('budgetTotalRaw', 0):,.0f} is split between "
                    "clippers by share of views (no fixed rate)")
    # Pool campaigns list platforms with no rate, so only show entries that carry one.
    rated = [p for p in (detail or {}).get("payout_model", []) if p.get("rate") is not None]
    if rated:
        per = ", ".join(
            f"{p.get('platform')} ${p['rate'] / 100:.2f}"
            + (f" (max ${p['maxPayout'] / 100:.0f})" if p.get("maxPayout") else "")
            for p in rated)
        lines.append(f"Per platform: {per}")
    lines.append(board.CAMPAIGN_URL.format(id=c["id"]))
    return "\n".join(lines)


def scan_once(notify: bool = True) -> dict:
    campaigns = board.fetch_board()
    if not campaigns:
        raise RuntimeError("Board returned no campaigns: the page format may have changed.")
    ts = db.now()
    alerts: list[tuple[dict, list[str]]] = []

    with db.connect() as conn:
        first_run = conn.execute("SELECT COUNT(*) FROM campaigns").fetchone()[0] == 0
        for c in campaigns:
            hits = keyword_hits(c)
            row = conn.execute("SELECT matched_keywords FROM campaigns WHERE id=?",
                               (c["id"],)).fetchone()
            tags: list[str] = []
            if row is None:
                # On the very first run everything is "new", so only keywords alert.
                if not first_run:
                    tags.append("NEW")
                    if (c.get("payoutSortRaw") or 0) >= HIGH_RATE:
                        tags.append("HIGH PAY")
                if hits:
                    tags.append("KEYWORD " + ",".join(hits))
            elif hits and set(hits) - set(db.loads(row["matched_keywords"], [])):
                tags.append("KEYWORD " + ",".join(hits))
            if tags:
                alerts.append((c, tags))

            conn.execute(
                """INSERT INTO campaigns (id, title, brand, category, type, platforms, rate_per_1k,
                       budget_total, budget_available, requires_application, description,
                       launched_at_ms, matched_keywords, first_seen, last_seen)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET title=excluded.title, brand=excluded.brand,
                       category=excluded.category, platforms=excluded.platforms,
                       rate_per_1k=excluded.rate_per_1k, budget_total=excluded.budget_total,
                       budget_available=excluded.budget_available,
                       requires_application=excluded.requires_application,
                       description=excluded.description,
                       matched_keywords=excluded.matched_keywords, last_seen=excluded.last_seen""",
                (c["id"], c.get("title"), c.get("brand"), c.get("category"), c.get("type"),
                 db.dumps(c.get("platforms") or []), c.get("payoutSortRaw"),
                 c.get("budgetTotalRaw"), c.get("availableBudgetRaw"),
                 int(bool(c.get("requiresApplication"))), c.get("description"),
                 c.get("launchedAtMs"), db.dumps(hits), ts, ts))

    # Keyword matches first, then the rest; fetch detail pages slowly.
    alerts.sort(key=lambda a: not any(t.startswith("KEYWORD") for t in a[1]))
    for n, (c, tags) in enumerate(alerts):
        detail = None
        if n < MAX_DETAILS_PER_SCAN:
            try:
                time.sleep(DETAIL_DELAY_S)
                detail = board.fetch_campaign(c["id"])
            except Exception as exc:  # a detail failure must not lose the alert
                console.print(f"[yellow]detail fetch failed for {c['id']}: {exc}[/]")
            if detail:
                with db.connect() as conn:
                    conn.execute("UPDATE campaigns SET detail=? WHERE id=?",
                                 (db.dumps(detail), c["id"]))
        msg = _format(c, detail, tags)
        console.print(msg + "\n")
        if notify:
            telegram.send_text(msg)

    return {"campaigns": len(campaigns), "alerts": len(alerts), "first_run": first_run}


def scan_loop(interval_s: int = 3600) -> None:
    while True:
        try:
            r = scan_once()
            console.print(f"[dim]{time.strftime('%H:%M')} scanned {r['campaigns']} campaigns, "
                          f"{r['alerts']} alert(s)[/]")
        except Exception as exc:
            console.print(f"[red]scan failed: {exc}[/]")
            try:
                telegram.send_text(f"Campaign scanner error: {exc}")
            except Exception:
                pass
        time.sleep(interval_s)
