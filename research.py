import os
from urllib.parse import urlparse

from tavily import TavilyClient

from ingest import ingest_document
from models import IngestRequest

client = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))

HIGH_AUTHORITY_DOMAINS = {
    "sec.gov", "federalreserve.gov", "bls.gov",
    "crunchbase.com", "pitchbook.com", "techcrunch.com",
    "wsj.com", "ft.com", "bloomberg.com", "reuters.com",
    "hbr.org", "mckinsey.com", "bcg.com",
}

MIN_RELEVANCE_SCORE = 0.5
MIN_CONTENT_CHARS = 80


def _evidence_tier(url: str) -> str:
    try:
        domain = urlparse(url).netloc.lstrip("www.")
        if any(domain.endswith(h) for h in HIGH_AUTHORITY_DOMAINS):
            return "E2"
    except Exception:
        pass
    return "E1"


def _is_usable(result: dict) -> bool:
    score = result.get("score", 0.0)
    content = result.get("content", "")
    if score < MIN_RELEVANCE_SCORE:
        return False
    if len(content.strip()) < MIN_CONTENT_CHARS:
        return False
    return True


def research_gaps(queries: list[str]) -> list[str]:
    all_fact_ids: list[str] = []
    seen_urls: set[str] = set()

    for query in queries[:3]:
        response = client.search(
            query,
            max_results=5,
            include_answer=False,
        )
        results = response.get("results", [])

        for r in results:
            url = r.get("url", "")
            if url in seen_urls:
                continue
            seen_urls.add(url)

            if not _is_usable(r):
                continue

            tier = _evidence_tier(url)
            title = r.get("title") or url
            content = (
                f"Source: {url}\n"
                f"Relevance score: {r.get('score', 0):.2f}\n"
                f"Evidence tier assigned: {tier}\n\n"
                f"{r.get('content', '').strip()}"
            )

            req = IngestRequest(
                title=title,
                source_type="research",
                content=content,
                document_date=None,
                metadata={"evidence_tier_override": tier},
            )
            result = ingest_document(req)
            all_fact_ids.extend(result.get("fact_ids", []))

    return all_fact_ids
