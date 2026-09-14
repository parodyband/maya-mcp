"""Bounded, client-owned animation evidence with explicit lifetime."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import time
import uuid

from . import state

MAX_RECORDS = 32
MAX_BYTES = 128 * 1024 * 1024
MAX_RECORD_BYTES = 64 * 1024 * 1024
TTL = 3600
_records = {}


def _size(data, directory=None):
    size = len(json.dumps(data, allow_nan=False, separators=(",", ":")).encode("utf-8"))
    if directory:
        size += sum(p.stat().st_size for p in Path(directory).iterdir() if p.is_file())
    return size


def _remove(token):
    record = _records[token]
    if record.get("directory"):
        # Only internally allocated artifact roots are recorded here.
        directory = Path(record["directory"])
        if directory.exists():
            shutil.rmtree(directory)
    del _records[token]


def clear(reason="reset"):
    failures = []
    for token in list(_records):
        try:
            _remove(token)
        except OSError as error:
            failures.append(str(error))
    if failures:
        raise state.ToolError("ARTIFACT_CLEANUP_FAILED", "Could not clean up animation evidence", {"errors": failures})


state.register_lifecycle_cleanup(clear)


def expire():
    for token, record in list(_records.items()):
        if time.monotonic() - record["created"] >= TTL or record["epoch"] != state.scene_epoch():
            _remove(token)


def put(kind, data, directory=None):
    expire()
    size = _size(data, directory)
    if size > MAX_RECORD_BYTES or len(_records) >= MAX_RECORDS or sum(r["bytes"] for r in _records.values()) + size > MAX_BYTES:
        raise state.ToolError("ARTIFACT_LIMIT", "Release animation artifacts before capturing more", {"bytes": size, "max_record_bytes": MAX_RECORD_BYTES})
    token = "animation:" + uuid.uuid4().hex
    _records[token] = {"kind": kind, "data": data, "directory": directory, "bytes": size,
                       "owner": state.current_client_session(), "epoch": state.scene_epoch(), "created": time.monotonic()}
    return token


def get(token, kind=None):
    expire()
    record = _records.get(token)
    if record is None or record["owner"] != state.current_client_session() or (kind and record["kind"] != kind):
        raise state.ToolError("ARTIFACT_NOT_FOUND", "Animation handle is missing, expired, or belongs to another client")
    return record


def release(token):
    get(token)
    _remove(token)


def listing(kind=None):
    expire()
    return [{"artifact_id": token, "kind": r["kind"], "label": r["data"].get("label", ""),
             "bytes": r["bytes"], "expires_in_seconds": max(0, TTL-(time.monotonic()-r["created"]))}
            for token, r in _records.items() if r["owner"] == state.current_client_session() and (kind is None or kind == r["kind"])]


def add_notes(token, notes):
    record = get(token, "take")
    old = record["data"].get("notes", [])
    if len(old) + len(notes) > 200:
        raise state.ToolError("ARTIFACT_LIMIT", "An animation take supports at most 200 notes")
    candidate = {**record["data"], "notes": old + notes}
    size = _size(candidate, record["directory"])
    if size > MAX_RECORD_BYTES or sum(r["bytes"] for r in _records.values()) - record["bytes"] + size > MAX_BYTES:
        raise state.ToolError("ARTIFACT_LIMIT", "Animation note storage budget exceeded")
    record["data"], record["bytes"] = candidate, size
    return candidate["notes"]


def recount(token):
    record = get(token)
    size = _size(record["data"], record["directory"])
    if size > MAX_RECORD_BYTES or sum(r["bytes"] for r in _records.values())-record["bytes"]+size > MAX_BYTES:
        raise state.ToolError("ARTIFACT_LIMIT", "Updated review exceeds the artifact storage budget")
    record["bytes"] = size
