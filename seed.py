import json
import sys
import time
import urllib.error
import urllib.request

BASE_URL = "http://localhost:8090/brain"


def post_ingest(item: dict) -> dict:
    payload = json.dumps({
        "title":         item["title"],
        "source_type":   item["source_type"],
        "content":       item["content"],
        "document_date": item.get("document_date")
    }).encode()
    req = urllib.request.Request(
        f"{BASE_URL}/ingest",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read())


def main():
    with open("data/loomwork_corpus.json") as f:
        items = json.load(f)
    print(f"Seeding {len(items)} documents...\n")
    total_facts, total_contradictions = 0, 0
    for i, item in enumerate(items, 1):
        result = post_ingest(item)
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
