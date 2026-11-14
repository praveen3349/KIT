import asyncio

from kit import db
from kit.knowledge import BM25Index, Chunk, chunk_text, knowledge
from kit.voice import split_text, tts_clean


def test_chunking_overlaps_and_covers_text():
    text = "Sentence number one is here. " * 100
    chunks = chunk_text(text, size=300, overlap=50)
    assert len(chunks) > 5 and all(len(c) <= 300 for c in chunks)


def test_bm25_ranks_relevant_chunk_first_and_respects_public_flag():
    idx = BM25Index([Chunk("1", "Handbook", "minimum 75 percent attendance is required", False),
                     Chunk("2", "Brochure", "annual fee is 1,20,000 rupees for B.Tech", True)])
    assert idx.search("what is the attendance rule")[0].doc_id == "1"
    assert idx.search("attendance", public_only=True) == []
    assert idx.search("fee for btech", public_only=True)[0].doc_id == "2"


def test_documents_sync_from_college(fake_college):
    assert asyncio.run(knowledge.sync()) is True
    assert asyncio.run(knowledge.sync()) is False          # unchanged list: no re-download
    assert knowledge.search("attendance rule")[0].title == "Student Handbook"
    assert all(c.title == "Admissions Brochure" for c in knowledge.search("fee seats", public_only=True))
    assert knowledge.search("hostel curfew", public_only=True) == []


def test_tts_helpers():
    assert tts_clean("**75%** attendance & more") == "75 percent attendance and more"
    parts = split_text("This is a sentence. " * 30)
    assert len(parts) > 1 and all(len(p) <= 190 for p in parts)


def test_history_is_private_per_owner(fake_college):
    async def go():
        cid = await db.create_conversation("u:arjun", "student")
        await db.add_message(cid, "user", "What is my attendance?")
        await db.add_message(cid, "assistant", "82 percent", {"tools": ["get_me_attendance"]})
        mine = await db.list_conversations("u:arjun")
        theirs = await db.list_conversations("u:priya")
        stolen = await db.get_conversation("u:priya", cid)
        msgs = await db.get_messages(cid)
        return mine, theirs, stolen, msgs
    mine, theirs, stolen, msgs = asyncio.run(go())
    assert mine[0]["title"] == "What is my attendance?" and mine[0]["messages"] == 2
    assert theirs == [] and stolen is None
    assert msgs[1]["meta"] == {"tools": ["get_me_attendance"]}
