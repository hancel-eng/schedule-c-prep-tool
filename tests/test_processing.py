"""Reading in saved steps: stopping and continuing loses nothing, a failed
part is the only thing read again, duplicates are skipped, and everything
saved is plain JSON. The AI is a fake; the store is the in-memory one, which
serialises exactly like the database. Synthetic data only."""
import io
import json
import threading

import numpy as np
import pandas as pd
from reportlab.pdfgen import canvas

from core import processing
from core.ai_errors import AIExtractionError
from core.storage import MemoryStore, client_record, new_client_state, to_json_safe


def _pdf(pages: int) -> bytes:
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer)
    for page in range(pages):
        c.drawString(72, 720, f"Synthetic page {page + 1}")
        c.showPage()
    c.save()
    return buffer.getvalue()


def _doc(page: int):
    return {"document_kind": "bank_statement", "continues_from_previous_part": page > 0, "issuer_name": "Test Bank",
            "account_holder": None, "account_last4": "1111", "period_start": None if page else "2025-01-01",
            "period_end": "2025-01-31", "opening_balance": 100.0 if page == 0 else None, "closing_balance": None,
            "summary_totals": [], "check": None, "invoice_or_receipt": None, "what_it_is": None,
            "could_not_read": [],
            "transactions": [{"date": "2025-01-05", "description": f"Row from part {page}", "amount": 1.0,
                              "direction": "debit", "running_balance": None, "section_heading": None,
                              "check_number": None, "category_as_written": None}]}


class FakeAI:
    def __init__(self, fail_parts=()):
        self.calls = []
        self.fail_parts = set(fail_parts)
        self.lock = threading.Lock()

    def __call__(self, source, part, year):
        first_page = part.pdf_pages[0]
        with self.lock:
            self.calls.append((source.filename, first_page))
        if (source.filename, first_page) in self.fail_parts:
            raise AIExtractionError("OUT_OF_CREDIT", "No credit.", "Add credit.")
        return {"documents": [_doc(first_page // 4)], "_usage": {"input_tokens": 10, "output_tokens": 2}}


def _setup(files):
    store = MemoryStore()
    client_id = store.create_client("Test client", 2025)
    state = store.load_client(client_id)
    processing.queue_files(store, client_id, state, files)
    store.save_client(client_id, state)
    return store, client_id, state


def _save(store, client_id, state):
    return lambda: store.save_client(client_id, state)


def test_reading_stops_and_continues_without_reading_anything_twice():
    store, cid, state = _setup([("year.pdf", _pdf(12))])  # 3 parts
    ai = FakeAI()
    assert processing.read_next_batch(store, cid, 2025, call_ai=ai, limit=1) == 1
    assert processing.progress(store, cid)["parts_done"] == 1
    # "Stop", then a new session loads the client from the store and continues
    state = store.load_client(cid)
    while processing.read_next_batch(store, cid, 2025, call_ai=ai, limit=2):
        pass
    finished = processing.finish_ready_files(store, cid, state, _save(store, cid, state))
    assert finished == ["year.pdf"]
    assert sorted(ai.calls) == [("year.pdf", 0), ("year.pdf", 4), ("year.pdf", 8)]
    saved = store.load_client(cid)["files"]["year.pdf"]
    assert saved["error"] is None and len(saved["documents"]) == 1
    assert len(saved["documents"][0]["transactions"]) == 3
    assert saved["usage"] == {"input_tokens": 30, "output_tokens": 6}
    assert store.uploads(cid) == []  # bytes deleted once read


def test_a_failed_part_is_the_only_part_read_again():
    store, cid, state = _setup([("year.pdf", _pdf(12))])
    ai = FakeAI(fail_parts={("year.pdf", 4)})
    while processing.read_next_batch(store, cid, 2025, call_ai=ai):
        pass
    processing.finish_ready_files(store, cid, state, _save(store, cid, state))
    saved = store.load_client(cid)["files"]["year.pdf"]
    assert saved["error"]["code"] == "OUT_OF_CREDIT" and saved["error"]["where"] == "pages 5-8 of 12"
    assert saved["documents"] == []
    assert processing.progress(store, cid)["failed_files"] == ["year.pdf"]
    assert processing.read_next_batch(store, cid, 2025, call_ai=ai) == 0  # waits for Try again

    ai.fail_parts.clear()
    ai.calls.clear()
    assert processing.retry_file(store, cid, "year.pdf")
    while processing.read_next_batch(store, cid, 2025, call_ai=ai):
        pass
    processing.finish_ready_files(store, cid, state, _save(store, cid, state))
    assert ai.calls == [("year.pdf", 4)]
    saved = store.load_client(cid)["files"]["year.pdf"]
    assert saved["error"] is None and len(saved["documents"][0]["transactions"]) == 3


def test_duplicate_and_unopenable_files_are_flagged_not_queued():
    store, cid, state = _setup([("a.pdf", _pdf(1))])
    result = processing.queue_files(store, cid, state, [("A.PDF", _pdf(1)), ("empty.pdf", b""), ("b.pdf", _pdf(2))])
    assert result["queued"] == ["b.pdf"]
    assert [d["filename"] for d in result["duplicates"]] == ["A.PDF"]
    assert state["files"]["empty.pdf"]["error"]["code"] == "EMPTY_FILE"


def test_removing_files_and_stopping_waiting_ones():
    store, cid, state = _setup([("a.pdf", _pdf(1)), ("b.pdf", _pdf(1))])
    ai = FakeAI()
    processing.read_next_batch(store, cid, 2025, call_ai=ai, limit=1)
    processing.finish_ready_files(store, cid, state, _save(store, cid, state))
    assert "a.pdf" in state["files"]
    assert processing.stop_waiting_files(store, cid) == ["b.pdf"]
    processing.remove_file(store, cid, state, "a.pdf")
    assert state["files"] == {} and store.uploads(cid) == []


def test_a_part_still_in_the_air_is_not_sent_again():
    store, cid, _ = _setup([("a.pdf", _pdf(8))])
    with processing._in_flight_lock:
        processing._in_flight.add((cid, "a.pdf", 0))
    try:
        assert processing.next_parts(store, cid, 4) == [("a.pdf", 1)]
    finally:
        with processing._in_flight_lock:
            processing._in_flight.discard((cid, "a.pdf", 0))


def test_saved_client_is_plain_json_even_with_pandas_and_numpy_values():
    state = new_client_state("X", 2025)
    state["answered_questions"] = [{"amount": np.float64(12.5), "count": np.int64(3), "when": pd.Timestamp("2025-01-02"),
                                    "missing": float("nan"), "na": pd.NA, "keys": ("a", "b")}]
    record = client_record(state)
    json.dumps(record, allow_nan=False)
    answer = record["answered_questions"][0]
    assert answer["amount"] == 12.5 and answer["count"] == 3 and answer["missing"] is None
    assert answer["na"] is None and answer["keys"] == ["a", "b"]
    assert answer["when"].startswith("2025-01-02")
    assert to_json_safe(float("inf")) is None


def test_clients_are_listed_newest_first_and_deleted_with_their_files():
    store = MemoryStore()
    first = store.create_client("First", 2025)
    second = store.create_client("Second", 2024)
    assert [c["client_name"] for c in store.list_clients()] == ["Second", "First"]
    store.add_upload(first, "a.pdf", b"x", 1)
    store.delete_client(first)
    assert [c["client_id"] for c in store.list_clients()] == [second]
    assert store.uploads(first) == []
