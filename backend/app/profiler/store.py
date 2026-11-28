"""Profile cache + background profiling worker.

Profiles are saved as <data_dir>/profiles/<source_id>.json, so a restart does not
re-profile. New sources are profiled in the background right after they are added.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.config import get_settings
from app.llm import client as llm
from app.profiler.describe import carry_over_descriptions, describe_profile
from app.profiler.models import SourceProfile
from app.profiler.profile import profile_source
from app.sources.registry import SourceError, get_registry

log = logging.getLogger(__name__)


# Saves and loads profiles, and runs profiling in the background.
class ProfileStore:
    def __init__(self, data_dir: str):
        self.dir = Path(data_dir) / "profiles"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, SourceProfile] = {}
        self._status: dict[str, str] = {}  # queued | profiling | describing | ready | error: ...
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="profiler")
        self._listeners: list = []

    # Lets other parts rebuild (search index, word list) when a profile changes.
    def on_change(self, fn) -> None:
        """Register a callback(source_id) fired when a profile is created/updated/removed."""
        self._listeners.append(fn)

    # Tells the listeners that a profile changed.
    def _notify(self, source_id: str) -> None:
        for fn in self._listeners:
            try:
                fn(source_id)
            except Exception:
                log.exception("profile listener failed")

    # Where a source's profile is saved on disk.
    def _path(self, source_id: str) -> Path:
        return self.dir / f"{source_id}.json"

    # Returns a source's profile (from memory or disk).
    def get(self, source_id: str) -> SourceProfile | None:
        with self._lock:
            if source_id in self._cache:
                return self._cache[source_id]
        p = self._path(source_id)
        if p.exists():
            prof = SourceProfile.model_validate_json(p.read_text())
            with self._lock:
                self._cache[source_id] = prof
                self._status.setdefault(source_id, "ready")
            return prof
        return None

    # Profiling status: queued, profiling, describing, ready or error.
    def status(self, source_id: str) -> str:
        if source_id in self._status:
            return self._status[source_id]
        return "ready" if self._path(source_id).exists() else "missing"

    # Writes a profile to disk.
    def _save(self, prof: SourceProfile) -> None:
        tmp = self._path(prof.source_id).with_suffix(".tmp")
        tmp.write_text(prof.model_dump_json())
        tmp.replace(self._path(prof.source_id))
        with self._lock:
            self._cache[prof.source_id] = prof

    # Profiles one source, then asks the LLM for descriptions.
    def _run(self, source_id: str) -> None:
        reg = get_registry()
        try:
            source = reg.get(source_id)
        except SourceError:
            return  # removed while queued
        try:
            previous = self.get(source_id)
            self._status[source_id] = "profiling"
            prof = profile_source(source, reg.engine(source_id))
            if previous is not None:
                carry_over_descriptions(previous, prof)
            self._save(prof)
            self._notify(source_id)
            if llm.available():
                self._status[source_id] = "describing"
                prof = describe_profile(prof)
                self._save(prof)
                self._notify(source_id)
            self._status[source_id] = "ready"
            log.info("profiled %s: %d tables in %.1fs", source.name, len(prof.tables), prof.seconds)
        except Exception as exc:
            log.exception("profiling %s failed", source_id)
            self._status[source_id] = f"error: {str(exc).splitlines()[0][:150]}"

    # Queues a source for profiling in the background.
    def schedule(self, source_id: str) -> None:
        self._status[source_id] = "queued"
        self._pool.submit(self._run, source_id)

    # On startup: profiles any source that has no profile yet.
    def ensure_all(self) -> None:
        """Profile every registered source that has no profile yet (or no descriptions)."""
        for s in get_registry().list():
            prof = self.get(s.id)
            if prof is None or (not prof.described and llm.available()):
                self.schedule(s.id)

    # Deletes a source's profile.
    def remove(self, source_id: str) -> None:
        with self._lock:
            self._cache.pop(source_id, None)
            self._status.pop(source_id, None)
        self._path(source_id).unlink(missing_ok=True)
        self._notify(source_id)


_store: ProfileStore | None = None


# Gives every file the same ProfileStore (created once).
def get_store() -> ProfileStore:
    global _store
    if _store is None:
        _store = ProfileStore(get_settings().data_dir)
    return _store
