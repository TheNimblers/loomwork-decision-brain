import json
import time

from db import get_db, init_db
from entities import seed_entities
from ingest import ingest_document
from models import IngestRequest


def main():
    init_db()
    db = get_db()
    seed_entities(db)
    db.close()

    with open("data/loomwork_corpus.json") as f:
        items = json.load(f)
    print(f"Seeding {len(items)} documents...\n")

    total_facts, total_contradictions = 0, 0
    for i, item in enumerate(items, 1):
        req = IngestRequest(
            title=item["title"],
            source_type=item["source_type"],
            content=item["content"],
            document_date=item.get("document_date"),
        )
        result = ingest_document(req)
        facts = result.get("facts_extracted", 0)
        contras = result.get("contradictions_detected", 0)
        total_facts += facts
        total_contradictions += contras
        status = "already existed" if result.get("already_existed") else f"{facts} facts, {contras} contradictions"
        flag = " <- contradiction [HIGH]" if contras > 0 else ""
        print(f"  OK [{i:2d}/{len(items)}] {item['title'][:55]:<55} {status}{flag}")
        time.sleep(0.5)
    print(f"\nSeed complete. {len(items)} sources {total_facts} facts {total_contradictions} contradictions detected.")


if __name__ == "__main__":
    main()
