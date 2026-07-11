import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from db import get_db, init_db
from decision import answer_question
from entities import seed_entities
from ingest import ingest_document
from models import DecideRequest, IngestRequest, QueryRequest
import memory

load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    db = get_db()
    init_db()
    seed_entities(db)
    db.close()
    yield


app = FastAPI(title="Decision Brain", version="1.0.0",
              docs_url="/brain/docs", openapi_url="/brain/openapi.json",
              lifespan=lifespan)
app.mount("/brain/static", StaticFiles(directory="static"), name="static")


@app.get("/brain/")
async def ui():
    return FileResponse("static/index.html", media_type="text/html; charset=utf-8")


@app.get("/brain/healthz")
async def healthz():
    db = get_db()
    try:
        facts_count = db.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
        decisions_count = db.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
        contradictions_count = db.execute("SELECT COUNT(*) FROM contradictions").fetchone()[0]
        return {"status": "ok", "facts_count": facts_count,
                "decisions_count": decisions_count,
                "contradictions_count": contradictions_count}
    finally:
        db.close()


@app.post("/brain/ingest")
async def ingest(req: IngestRequest):
    return ingest_document(req)


@app.get("/brain/facts")
async def list_facts(fact_type: str | None = None, query: str | None = None,
                     limit: int = 50, include_contested: bool = True):
    return memory.get_all_facts(fact_type, query, limit, include_contested)


@app.get("/brain/contradictions")
async def list_contradictions():
    return memory.get_all_contradictions()


@app.get("/brain/entities")
async def list_entities():
    db = get_db()
    try:
        return [dict(r) for r in db.execute(
            "SELECT e.*, COUNT(em.fact_id) as mention_count "
            "FROM entities e LEFT JOIN entity_mentions em ON e.id = em.entity_id "
            "GROUP BY e.id ORDER BY mention_count DESC"
        ).fetchall()]
    finally:
        db.close()


@app.post("/brain/query")
async def query(req: QueryRequest):
    if not req.question.strip():
        raise HTTPException(status_code=422, detail="question cannot be empty")
    return answer_question(req.question)


@app.get("/brain/decisions")
async def list_decisions():
    return memory.get_all_decisions()


@app.get("/brain/decisions/{decision_id}")
async def get_decision(decision_id: str):
    record = memory.get_decision(decision_id)
    if not record:
        raise HTTPException(status_code=404, detail="decision not found")
    return record


@app.post("/brain/decisions/{decision_id}/decide")
async def decide(decision_id: str, req: DecideRequest):
    record = memory.get_decision(decision_id)
    if not record:
        raise HTTPException(status_code=404, detail="decision not found")
    if record["human_decision"] is not None:
        raise HTTPException(status_code=409,
                            detail=f"already decided: {record['human_decision']}")
    db = get_db()
    try:
        db.execute(
            "UPDATE decisions SET human_decision = ?, decision_note = ? WHERE id = ?",
            (req.decision, req.note, decision_id)
        )
        db.commit()
    finally:
        db.close()
    return memory.get_decision(decision_id)
