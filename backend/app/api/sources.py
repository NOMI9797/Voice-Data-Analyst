import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from app.profiler.store import get_store
from app.sources.registry import SourceError, get_registry

router = APIRouter(prefix="/sources", tags=["sources"])

MAX_UPLOAD_BYTES = 200 * 1024 * 1024
SQLITE_EXT = {".db", ".sqlite", ".sqlite3"}
CSV_EXT = {".csv", ".tsv", ".txt"}


class ConnectionIn(BaseModel):
    name: str = ""
    url: str


class SourcePatch(BaseModel):
    active: bool | None = None
    name: str | None = None


def _with_tables(s) -> dict:
    out = s.public()
    out["profile_status"] = get_store().status(s.id)
    try:
        tables = get_registry().tables(s.id)
        out.update(status="ok", tables=tables, error=None)
    except SourceError as exc:
        out.update(status="error", tables=[], error=str(exc))
    return out


@router.get("")
async def list_sources() -> list[dict]:
    return await run_in_threadpool(lambda: [_with_tables(s) for s in get_registry().list()])


@router.post("", status_code=201)
async def add_connection(body: ConnectionIn) -> dict:
    try:
        s = await run_in_threadpool(get_registry().add_connection, body.name, body.url)
    except SourceError as exc:
        raise HTTPException(400, str(exc)) from exc
    get_store().schedule(s.id)
    return await run_in_threadpool(_with_tables, s)


@router.post("/upload", status_code=201)
async def upload(files: list[UploadFile] = File(...), name: str = Form("")) -> dict:
    exts = {Path(f.filename or "").suffix.lower() for f in files}
    if exts <= SQLITE_EXT and len(files) == 1:
        kind = "sqlite"
    elif exts <= CSV_EXT:
        kind = "csv"
    else:
        raise HTTPException(400, "Upload either one SQLite file (.db/.sqlite) or one or more CSV files.")

    tmp_dir = Path(tempfile.mkdtemp())
    saved: list[tuple[str, Path]] = []
    try:
        for f in files:
            dest = tmp_dir / Path(f.filename or "upload").name
            size = 0
            with open(dest, "wb") as out:
                while chunk := await f.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_UPLOAD_BYTES:
                        raise HTTPException(413, f"{f.filename} is larger than 200 MB.")
                    out.write(chunk)
            saved.append((f.filename or dest.name, dest))

        reg = get_registry()
        try:
            if kind == "sqlite":
                s = await run_in_threadpool(reg.add_sqlite_upload, name, saved[0][0], saved[0][1])
            else:
                s = await run_in_threadpool(reg.add_csv_upload, name, saved)
        except SourceError as exc:
            raise HTTPException(400, str(exc)) from exc
        get_store().schedule(s.id)
        return await run_in_threadpool(_with_tables, s)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@router.patch("/{source_id}")
def update_source(source_id: str, body: SourcePatch) -> dict:
    reg = get_registry()
    try:
        s = reg.get(source_id)
        if body.active is not None:
            s = reg.set_active(source_id, body.active)
        if body.name is not None:
            s = reg.rename(source_id, body.name)
    except SourceError as exc:
        raise HTTPException(404, str(exc)) from exc
    return s.public()


@router.delete("/{source_id}", status_code=204)
def delete_source(source_id: str) -> None:
    try:
        get_registry().remove(source_id)
    except SourceError as exc:
        raise HTTPException(404, str(exc)) from exc
    get_store().remove(source_id)
