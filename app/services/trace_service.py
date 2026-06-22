"""
In-process live trace feed: bounded ring buffer + per-subscriber
asyncio.Queue, matching ARCHITECTURE.md section 2.6. NiceGUI's live trace
view subscribes via ui.timer.
"""
import asyncio
from collections import deque

_RING_BUFFER_SIZE = 500
_recent_spans: deque = deque(maxlen=_RING_BUFFER_SIZE)
_subscribers: list[asyncio.Queue] = []


def ingest_span(span: dict):
    _recent_spans.append(span)
    for q in _subscribers:
        q.put_nowait(span)


def subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue()
    _subscribers.append(q)
    return q


def unsubscribe(q: asyncio.Queue):
    if q in _subscribers:
        _subscribers.remove(q)


def get_recent(limit: int = 100):
    return list(_recent_spans)[-limit:]
