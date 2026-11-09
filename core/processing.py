"""Reading files in small saved steps, so a stop, a closed tab, or a crash
never loses what was already read.

  queue_files       saves the uploaded files and splits each into parts
  read_next_batch   reads a few parts at once; every part's answer is saved
                    the moment it arrives (by the worker thread itself, so it
                    is kept even if the page stops waiting for it)
  finish_ready_files once every part of a file is read, joins the parts into
                    documents, saves them into the client's workpaper, and
                    deletes the file's bytes
  retry_file / remove_file  for a file the AI could not read

No Streamlit here; app.py draws the progress and the Stop button.
"""
import threading
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Any, Callable, Dict, List, Optional, Tuple

from core import ai_reader, workpaper
from core.ai_errors import AIExtractionError

PARTS_PER_STEP = ai_reader.PARALLEL_CALLS

# Parts being read right now in this app process. A part that is still in
# the air after the person pressed Stop is not sent again by a quick
# "Continue"; its answer is saved when it arrives.
_in_flight: set = set()
_in_flight_lock = threading.Lock()


def _normal_name(filename: str) -> str:
    return filename.replace("\\", "/").split("/")[-1].strip().lower()


def queue_files(store, client_id: str, state: Dict[str, Any], uploads: List[Tuple[str, bytes]]) -> Dict[str, Any]:
    """Saves new files to be read. A file whose name was already uploaded for
    this client is skipped and flagged, so nothing is counted twice. A file
    that can't be opened at all is recorded right away with its error.
    Changes `state` (the caller saves it). Returns {"queued", "duplicates", "unreadable"}."""
    known = {_normal_name(f) for f in state.get("files") or {}}
    known |= {_normal_name(u["filename"]) for u in store.uploads(client_id)}
    queued, duplicates, unreadable = [], [], {}
    for filename, raw_bytes in uploads:
        if _normal_name(filename) in known:
            duplicates.append({"filename": filename,
                               "reason": f"A file named '{filename}' was already uploaded for this client, "
                                         "so this copy was skipped and nothing is counted twice.",
                               "status": "Skipped (Duplicate Filename)"})
            continue
        known.add(_normal_name(filename))
        try:
            source = ai_reader.plan_parts(raw_bytes, filename)
        except AIExtractionError as e:
            unreadable[filename] = {"documents": [], "error": e.to_dict(), "usage": {}, "part_count": 0}
            continue
        store.add_upload(client_id, filename, raw_bytes, len(source.parts))
        queued.append(filename)
    state.setdefault("duplicates_flagged", []).extend(duplicates)
    if unreadable:
        state.setdefault("files", {}).update(workpaper.ingest(unreadable))
    return {"queued": queued, "duplicates": duplicates, "unreadable": list(unreadable)}


def progress(store, client_id: str) -> Dict[str, Any]:
    """How far reading has got, counting only files still waiting (not failed)."""
    uploads = store.uploads(client_id)
    done = store.done_parts(client_id)
    waiting = [u for u in uploads if not u["failed"]]
    total = sum(u["part_count"] for u in waiting)
    finished = sum(min(len(done.get(u["filename"], ())), u["part_count"]) for u in waiting)
    return {"files_waiting": len(waiting), "parts_total": total, "parts_done": finished,
            "waiting_files": [u["filename"] for u in waiting],
            "failed_files": [u["filename"] for u in uploads if u["failed"]]}


def _error_dict(exc: Exception, where: str) -> Dict[str, Any]:
    if isinstance(exc, AIExtractionError):
        return {**exc.to_dict(), "where": where}
    return {"code": "UNKNOWN_AI_ERROR", "message": "Something unexpected went wrong while reading this file.",
            "fix": "Click Try again. If it keeps failing, send the technical detail to whoever maintains the app.",
            "detail": f"{type(exc).__name__}: {exc}", "where": where}


def _read_and_save(store, client_id, source, index, part, tax_year, call_ai) -> None:
    key = (client_id, source.filename, index)
    try:
        try:
            result = {"label": part.label, "payloads": ai_reader.read_part(source, part, tax_year, call_ai)}
        except Exception as exc:  # recorded, never raised: one bad part must not stop the others
            result = {"label": part.label, "error": _error_dict(exc, part.label)}
        store.save_part_result(client_id, source.filename, index, result)
    finally:
        with _in_flight_lock:
            _in_flight.discard(key)


def next_parts(store, client_id: str, limit: int) -> List[Tuple[str, int]]:
    """The next (filename, part_index) pairs to read, oldest file first, so
    files finish one by one instead of all at the very end."""
    done = store.done_parts(client_id)
    chosen = []
    with _in_flight_lock:
        busy = set(_in_flight)
    for upload in store.uploads(client_id):
        if upload["failed"]:
            continue
        for index in range(upload["part_count"]):
            if index in done.get(upload["filename"], ()) or (client_id, upload["filename"], index) in busy:
                continue
            chosen.append((upload["filename"], index))
            if len(chosen) >= limit:
                return chosen
    return chosen


def read_next_batch(store, client_id: str, tax_year: Optional[int], *, call_ai: Callable = None,
                    limit: int = PARTS_PER_STEP, on_tick: Optional[Callable[[int, str], None]] = None) -> int:
    """Reads up to `limit` parts at the same time and returns how many were
    started. on_tick(parts_done_in_this_step, message) is called from this thread about once a second
    while waiting, and after each part; app.py uses it to redraw progress,
    which is also the moment Streamlit can stop this run when Stop is pressed.
    Parts already sent keep going in the background and save themselves."""
    chosen = next_parts(store, client_id, limit)
    if not chosen:
        return 0
    sources = {}
    for filename in {f for f, _ in chosen}:
        raw = store.upload_bytes(client_id, filename)
        if raw is not None:
            sources[filename] = ai_reader.plan_parts(raw, filename)
    tasks = [(f, i) for f, i in chosen if f in sources and i < len(sources[f].parts)]
    with _in_flight_lock:
        _in_flight.update((client_id, f, i) for f, i in tasks)

    pool = ThreadPoolExecutor(max_workers=max(1, len(tasks)))
    try:
        futures = {pool.submit(_read_and_save, store, client_id, sources[f], i, sources[f].parts[i],
                               tax_year, call_ai): (f, i) for f, i in tasks}
        pending, done_count = set(futures), 0
        while pending:
            finished, pending = wait(pending, timeout=1.0, return_when=FIRST_COMPLETED)
            done_count += len(finished)
            if on_tick:
                names = ", ".join(f"{f} ({sources[f].parts[i].label})" for f, i in (futures[x] for x in finished))
                on_tick(done_count, f"Finished {names}" if names else "")
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return len(tasks)


def finish_ready_files(store, client_id: str, state: Dict[str, Any], save_state: Callable[[], None]) -> List[str]:
    """For every waiting file whose parts are all read: joins the parts into
    documents and saves them into the client's workpaper; then deletes the
    file's bytes (only after the workpaper is saved). A file with a failed
    part keeps its bytes and its good parts, gets its error recorded, and
    waits for Try again, which re-reads only the failed parts.
    Returns the names of the files finished (read or failed) just now."""
    done = store.done_parts(client_id)
    ready = [u for u in store.uploads(client_id)
             if not u["failed"] and len(done.get(u["filename"], ())) >= u["part_count"]]
    if not ready:
        return []
    finished, succeeded, failed = {}, [], []
    for upload in ready:
        filename = upload["filename"]
        results = store.part_results(client_id, filename)
        usage = {"input_tokens": 0, "output_tokens": 0}
        payloads, error, failed_indexes = [], None, []
        for index in sorted(results):
            result = results[index]
            if result.get("error"):
                failed_indexes.append(index)
                error = error or result["error"]
                continue
            for payload in result.get("payloads") or []:
                payloads.append(payload)
                for key in usage:
                    usage[key] += (payload.get("_usage") or {}).get(key, 0)
        documents = [] if error else ai_reader.merge_parts(filename, payloads)
        finished[filename] = {"documents": documents, "error": error, "usage": usage,
                              "part_count": upload["part_count"]}
        (failed if error else succeeded).append((filename, failed_indexes))

    state.setdefault("files", {}).update(workpaper.ingest(finished))
    save_state()
    for filename, _ in succeeded:
        store.delete_upload(client_id, filename)
    for filename, failed_indexes in failed:
        for index in failed_indexes:
            store.delete_part_result(client_id, filename, index)
        store.set_upload_failed(client_id, filename, True)
    return list(finished)


def retry_file(store, client_id: str, filename: str) -> bool:
    """Puts a failed file back in line. Returns False if its bytes are gone."""
    if store.upload_bytes(client_id, filename) is None:
        return False
    store.set_upload_failed(client_id, filename, False)
    return True


def remove_file(store, client_id: str, state: Dict[str, Any], filename: str) -> None:
    """Removes a file and everything read from it (the caller saves state).
    Hand-correction log entries are kept as history."""
    store.delete_upload(client_id, filename)
    (state.get("files") or {}).pop(filename, None)


def stop_waiting_files(store, client_id: str) -> List[str]:
    """Removes every file still waiting to be read (not the ones already read)."""
    removed = []
    for upload in store.uploads(client_id):
        if not upload["failed"]:
            store.delete_upload(client_id, upload["filename"])
            removed.append(upload["filename"])
    return removed
