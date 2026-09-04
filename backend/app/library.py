"""P4-A4 — research library: SEC EDGAR ingestion + FTS5 full-text search.

Grounding without external embedding APIs: SQLite FTS5 (porter stemming) gives
BM25-ranked retrieval over SEC filings and the user's own reports. Retrieval is
point-in-time filtered (documents dated after the analysis date are excluded).
"""
from __future__ import annotations

import asyncio
import logging
import re

import httpx
from sqlalchemy import text as sql

from .db import SessionLocal, engine
from .models import Document

log = logging.getLogger("agentalgo.library")

_UA = {"User-Agent": "AgentAlgo research tool (contact: admin@agentalgo.dev)"}
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
MAX_DOC_CHARS = 60_000
_FTS_QUERY_SANITIZE = re.compile(r'[^\w\s"]')


def _index(doc: Document) -> None:
    with engine.begin() as conn:
        conn.execute(
            sql("INSERT INTO documents_fts (content, title, doc_id) VALUES (:c, :t, :d)"),
            {"c": doc.content, "t": doc.title, "d": doc.id},
        )


def add_document(user_id: str, ticker: str, source: str, title: str,
                 doc_date: str, url: str, content: str) -> Document:
    with SessionLocal() as db:
        doc = Document(
            user_id=user_id, ticker=ticker, source=source, title=title[:255],
            doc_date=doc_date, url=url[:512], content=content[:MAX_DOC_CHARS],
        )
        db.add(doc)
        db.commit()
        _index(doc)
        return doc


def search(user_id: str, query: str, ticker: str | None = None,
           as_of: str | None = None, limit: int = 8) -> list[dict]:
    """BM25-ranked snippets from the user's library."""
    safe = _FTS_QUERY_SANITIZE.sub(" ", query).strip()
    if not safe:
        return []
    with engine.connect() as conn:
        rows = conn.execute(
            sql(
                "SELECT f.doc_id, snippet(documents_fts, 0, '«', '»', ' … ', 24) AS snip, "
                "bm25(documents_fts) AS score FROM documents_fts f "
                "WHERE documents_fts MATCH :q ORDER BY score LIMIT 50"
            ),
            {"q": safe},
        ).fetchall()
    if not rows:
        return []
    snippets = {r[0]: r[1] for r in rows}
    order = [r[0] for r in rows]
    with SessionLocal() as db:
        docs = db.query(Document).filter(Document.id.in_(order), Document.user_id == user_id)
        if ticker:
            docs = docs.filter(Document.ticker == ticker)
        if as_of:
            docs = docs.filter((Document.doc_date == "") | (Document.doc_date <= as_of))
        by_id = {d.id: d for d in docs.all()}
    out = []
    for doc_id in order:
        d = by_id.get(doc_id)
        if d is None:
            continue
        out.append({
            "id": d.id, "ticker": d.ticker, "source": d.source, "title": d.title,
            "doc_date": d.doc_date, "url": d.url, "snippet": snippets[doc_id],
        })
        if len(out) >= limit:
            break
    return out


def grounding_context(user_id: str, ticker: str, as_of: str, limit: int = 2) -> list[dict]:
    """Top library passages for an analysis (used by the fundamentals stage).

    FTS5 ANDs terms by default; grounding wants best-effort recall, so OR them.
    """
    base = ticker.split(".")[0].split("-")[0]
    query = f"{base} OR revenue OR risk OR outlook OR guidance"
    return search(user_id, query, ticker=ticker, as_of=as_of, limit=limit)


# ---------- SEC EDGAR ingestion ----------

async def ingest_sec_filings(user_id: str, ticker: str, max_filings: int = 3) -> list[dict]:
    """Fetch the latest 10-K/10-Q for `ticker` from EDGAR into the user's library."""
    async with httpx.AsyncClient(timeout=30, headers=_UA, follow_redirects=True) as client:
        r = await client.get("https://www.sec.gov/files/company_tickers.json")
        r.raise_for_status()
        cik = None
        base = ticker.split(".")[0]  # EDGAR is US listings
        for row in r.json().values():
            if row["ticker"].upper() == base.upper():
                cik = int(row["cik_str"])
                break
        if cik is None:
            raise ValueError(f"{ticker} not found on SEC EDGAR (US listings only)")

        r = await client.get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")
        r.raise_for_status()
        recent = r.json()["filings"]["recent"]
        ingested = []
        with SessionLocal() as db:
            existing_urls = {
                d.url for d in db.query(Document)
                .filter(Document.user_id == user_id, Document.ticker == ticker,
                        Document.source == "sec_filing").all()
            }
        for form, acc, doc, filed in zip(
            recent["form"], recent["accessionNumber"], recent["primaryDocument"], recent["filingDate"]
        ):
            if form not in ("10-K", "10-Q") or len(ingested) >= max_filings:
                continue
            url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{doc}"
            if url in existing_urls:
                continue
            try:
                fr = await client.get(url)
                fr.raise_for_status()
                text_content = _WS_RE.sub(" ", _TAG_RE.sub(" ", fr.text))
                await asyncio.to_thread(
                    add_document, user_id, ticker, "sec_filing",
                    f"{base} {form} filed {filed}", filed, url, text_content,
                )
                ingested.append({"form": form, "filed": filed, "url": url})
            except httpx.HTTPError as exc:
                log.info("EDGAR doc fetch failed %s: %s", url, exc)
        return ingested


def index_run_reports(user_id: str, run_id: str) -> None:
    """Auto-index a finished run's reports into the library (best effort)."""
    from .models import Run, RunReport

    with SessionLocal() as db:
        run = db.get(Run, run_id)
        if run is None:
            return
        reports = db.query(RunReport).filter(RunReport.run_id == run_id).all()
        for r in reports:
            add_document(
                user_id, run.ticker, "report",
                f"{run.ticker} {r.section.replace('_', ' ')} ({run.trade_date})",
                run.trade_date, f"/runs/{run_id}", r.content_md,
            )
