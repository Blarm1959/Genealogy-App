import datetime as dt
import hashlib
import json
import os
import secrets
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form, Depends
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, select, event, or_, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import IntegrityError

from . import gedcom
from .models import Base, Workspace, Import, Person, Membership, Relationship, Entry, Document, Audit, LoginSession, uid, now
from .security import password_valid

ROOT = Path(__file__).resolve().parents[1]

class LoginBody(BaseModel):
    password: str = Field(min_length=1, max_length=256)
class CommitBody(BaseModel):
    workspace_ids: list[str] = Field(min_length=1, max_length=20)
    reviewed: bool
class MemberBody(BaseModel):
    workspace_ids: list[str] = Field(min_length=1, max_length=20)
class EntryBody(BaseModel):
    workspace_id: str
    person_id: str | None = None
    kind: str
    title: str = Field(min_length=1, max_length=300)
    text: str = Field(default='', max_length=100000)
    source_url: str = Field(default='', max_length=4000)
class EntryStatus(BaseModel):
    status: str
class RelationshipBody(BaseModel):
    parent_id: str
    child_id: str
    evidence: str = Field(min_length=10, max_length=100000)
    reviewed: bool


def create_app(config=None):
    if config is None:
        config_path = os.environ.get('GENEALOGY_CONFIG')
        if not config_path: raise RuntimeError('Set GENEALOGY_CONFIG to your private JSON configuration. See docs/SETUP.md.')
        config = json.loads(Path(config_path).read_text())
    if not config.get('password_hash'): raise RuntimeError('Owner password hash is required.')
    data_dir = Path(config['data_dir']).resolve()
    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    for folder in ('imports', 'documents'): (data_dir / folder).mkdir(exist_ok=True, mode=0o700)
    db_url = config['database_url']
    engine = create_engine(db_url, **({'connect_args': {'check_same_thread': False}} if db_url.startswith('sqlite:') else {}))
    if db_url.startswith('sqlite:'):
        @event.listens_for(engine, 'connect')
        def sqlite_fk(dbapi, _): dbapi.execute('PRAGMA foreign_keys=ON')
    sessions = sessionmaker(engine, expire_on_commit=False)
    attempts = defaultdict(deque)

    @asynccontextmanager
    async def lifespan(app):
        Base.metadata.create_all(engine)
        with sessions.begin() as s:
            for name in ('Gooding', 'Larman'):
                if not s.scalar(select(Workspace).where(Workspace.name == name)): s.add(Workspace(name=name))
        yield
        engine.dispose()

    app = FastAPI(title='Genealogy', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.engine = engine
    app.state.data_dir = data_dir

    def db():
        with sessions() as s: yield s

    def logged_in(request: Request, s=Depends(db)):
        token = request.cookies.get('genealogy_session', '')
        row = s.get(LoginSession, hashlib.sha256(token.encode()).hexdigest()) if token else None
        if not row or row.expires <= now(): raise HTTPException(401, 'Please sign in.')
        return row

    @app.middleware('http')
    async def guard(request, call_next):
        if request.url.path.startswith('/api/') and request.method not in ('GET', 'HEAD', 'OPTIONS'):
            try:
                if int(request.headers.get('content-length', '0')) > 26 * 1024 * 1024:
                    return JSONResponse({'detail': 'Request exceeds 26 MiB.'}, status_code=413)
            except ValueError:
                return JSONResponse({'detail': 'Invalid request length.'}, status_code=400)
            if request.headers.get('x-genealogy-request') != '1':
                return JSONResponse({'detail': 'Missing request protection header.'}, status_code=403)
            origin = request.headers.get('origin')
            if origin and urlsplit(origin).netloc != request.headers.get('host'):
                return JSONResponse({'detail': 'Cross-origin request refused.'}, status_code=403)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'self'"
        if request.url.path.startswith('/api/'): response.headers['Cache-Control'] = 'no-store'
        return response

    def audit(s, action, **detail): s.add(Audit(action=action, detail=detail))
    def workspace(s, id):
        if not s.get(Workspace, id): raise HTTPException(404, 'Workspace not found.')
    def linked_person(s, wid, pid):
        workspace(s, wid)
        if pid and not s.get(Membership, (wid, pid)): raise HTTPException(400, 'Person is not in this workspace.')
    def safe_url(url):
        if url and urlsplit(url).scheme not in ('https', 'http'): raise HTTPException(400, 'Source link must use http or https.')
    def summary(p):
        events = p.data.get('events', [])
        return {'id': p.id, 'name': p.name, 'status': p.status,
                'birth': next((e['date'] for e in events if e['type'] == 'BIRT'), ''),
                'death': next((e['date'] for e in events if e['type'] == 'DEAT'), ''),
                'place': next((e['place'] for e in events if e['type'] == 'BIRT'), '')}

    @app.get('/api/health')
    def health(): return {'status': 'ok'}

    @app.post('/api/login')
    def login(body: LoginBody, request: Request, s=Depends(db)):
        key = request.client.host if request.client else 'unknown'
        q = attempts[key]
        while q and q[0] < time.monotonic() - 300: q.popleft()
        if len(q) >= 8: raise HTTPException(429, 'Too many attempts. Wait five minutes.')
        q.append(time.monotonic())
        if not password_valid(body.password, config['password_hash']): raise HTTPException(401, 'Incorrect password.')
        q.clear()
        token = secrets.token_urlsafe(48)
        expiry = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=12)).isoformat()
        for old in s.scalars(select(LoginSession).where(LoginSession.expires <= now())): s.delete(old)
        s.add(LoginSession(token_hash=hashlib.sha256(token.encode()).hexdigest(), expires=expiry))
        s.commit()
        response = JSONResponse({'ok': True})
        response.set_cookie('genealogy_session', token, httponly=True, secure=config.get('secure_cookie', False), samesite='strict', max_age=43200)
        return response

    @app.post('/api/logout', dependencies=[Depends(logged_in)])
    def logout(request: Request, s=Depends(db)):
        row = s.get(LoginSession, hashlib.sha256(request.cookies['genealogy_session'].encode()).hexdigest())
        if row: s.delete(row)
        s.commit()
        response = JSONResponse({'ok': True}); response.delete_cookie('genealogy_session'); return response

    @app.get('/api/state', dependencies=[Depends(logged_in)])
    def state(s=Depends(db)):
        version = 'development'
        metadata = ROOT / 'release.json'
        if metadata.exists(): version = json.loads(metadata.read_text(encoding='utf-8-sig')).get('version', version)
        workspaces = [{'id': w.id, 'name': w.name, 'people': s.scalar(select(func.count()).select_from(Membership).where(Membership.workspace_id == w.id))} for w in s.scalars(select(Workspace).order_by(Workspace.name))]
        return {'version': version, 'workspaces': workspaces,
                'imports': [{'id': i.id, 'filename': i.filename, 'status': i.status, 'created': i.created, 'report': i.parsed['report']} for i in s.scalars(select(Import).order_by(Import.created.desc()))],
                'providers': [
                    {'name': 'WikiTree', 'type': 'Contributed tree profiles', 'status': 'Pending terms review and live connector test'},
                    {'name': 'UK National Archives', 'type': 'Archive catalogue descriptions', 'status': 'Pending terms review and live connector test'},
                    {'name': 'Geni', 'type': 'Contributed tree profiles', 'status': 'Application registration, OAuth and endpoint access needed'},
                    {'name': 'FamilySearch', 'type': 'Family tree API', 'status': 'Not included: approval uncertain; no historical-record search'},
                    {'name': 'Ancestry', 'type': 'GEDCOM and manual evidence uploads', 'status': 'No supported public search API identified'}]}

    @app.post('/api/imports', dependencies=[Depends(logged_in)])
    async def preview(file: UploadFile = File(), s=Depends(db)):
        raw = await file.read(gedcom.MAX_FILE + 1)
        name = Path((file.filename or 'tree.ged').replace('\\', '/')).name[:250]
        try:
            content, ged_name, extras = gedcom.gedcom_bytes(raw, name)
            parsed = gedcom.parse(content)
        except (ValueError, UnicodeError) as e: raise HTTPException(400, str(e))
        digest = hashlib.sha256(raw).hexdigest()
        existing = s.scalar(select(Import).where(Import.sha256 == digest))
        if existing: return {'id': existing.id, 'status': existing.status, 'report': existing.parsed['report'], 'duplicate': True}
        parsed['report']['gedcom_filename'] = ged_name
        parsed['report']['other_zip_files'] = extras
        if extras: parsed['report']['warnings'].append('Additional ZIP files retained inside original upload; upload wanted documents separately.')
        previous = s.scalars(select(Person)).all()
        known = {(p.name.casefold(), summary(p)['birth']) for p in previous}
        parsed['report']['possible_duplicates'] = sum((p['name'].casefold(), next((e['date'] for e in p['events'] if e['type'] == 'BIRT'), '')) in known for p in parsed['persons'])
        id = uid(); stored = f'imports/{id}.bin'
        (data_dir / stored).write_bytes(raw)
        item = Import(id=id, filename=name, sha256=digest, storage=stored, parsed=parsed)
        s.add(item); audit(s, 'import_preview', import_id=id, filename=name, sha256=digest); s.commit()
        return {'id': id, 'status': 'preview', 'report': parsed['report'], 'duplicate': False}

    @app.post('/api/imports/{id}/commit', dependencies=[Depends(logged_in)])
    def commit_import(id: str, body: CommitBody, s=Depends(db)):
        if not body.reviewed: raise HTTPException(400, 'Review the import report before accepting.')
        item = s.scalar(select(Import).where(Import.id == id).with_for_update())
        if not item: raise HTTPException(404, 'Import not found.')
        if item.status != 'preview': raise HTTPException(409, 'Import already accepted; no duplicate people created.')
        wids = set(body.workspace_ids)
        for w in wids: workspace(s, w)
        mapping = {}
        for p in item.parsed['persons']:
            pid = uid(); mapping[p['xref']] = pid
            s.add(Person(id=pid, import_id=id, xref=p['xref'], name=p['name'], data=p))
        s.flush()
        for pid in mapping.values():
            for wid in wids: s.add(Membership(workspace_id=wid, person_id=pid))
        pairs = set()
        for family in item.parsed['families']:
            for parent in family['parents']:
                for child in family['children']:
                    if parent in mapping and child in mapping and parent != child:
                        pairs.add((mapping[parent], mapping[child]))
        for parent, child in pairs: s.add(Relationship(parent_id=parent, child_id=child))
        item.status = 'accepted'
        audit(s, 'import_accepted', import_id=id, workspace_ids=sorted(wids), people=len(mapping), relationships=len(pairs))
        s.commit(); return {'people': len(mapping), 'relationships': len(pairs)}

    @app.get('/api/imports/{id}/original', dependencies=[Depends(logged_in)])
    def original(id: str, s=Depends(db)):
        item = s.get(Import, id)
        if not item: raise HTTPException(404, 'Import not found.')
        return FileResponse(data_dir / item.storage, filename=item.filename, media_type='application/octet-stream')

    @app.get('/api/imports/{id}/preserved', dependencies=[Depends(logged_in)])
    def preserved(id: str, s=Depends(db)):
        item = s.get(Import, id)
        if not item: raise HTTPException(404, 'Import not found.')
        return JSONResponse(item.parsed, headers={'Content-Disposition': 'attachment; filename="preserved-tree.json"'})

    @app.get('/api/people', dependencies=[Depends(logged_in)])
    def people(workspace_id: str, q: str = '', place: str = '', year: int | None = None,
               spread: int = 5, relative: str = '', offset: int = 0, limit: int = 50, s=Depends(db)):
        workspace(s, workspace_id)
        offset = max(0, offset); limit = min(100, max(1, limit)); spread = min(100, max(0, spread))
        candidates = list(s.scalars(select(Person).join(Membership).where(Membership.workspace_id == workspace_id).order_by(Person.name)))
        variants = [x.strip().casefold() for x in q.split('|') if x.strip()]
        relatives = defaultdict(list)
        if relative:
            all_people = {p.id: p.name.casefold() for p in s.scalars(select(Person))}
            for r in s.scalars(select(Relationship)):
                relatives[r.parent_id].append(all_people.get(r.child_id, ''))
                relatives[r.child_id].append(all_people.get(r.parent_id, ''))
        results = []
        import re
        for p in candidates:
            names = ' '.join(p.data.get('names', [p.name])).casefold()
            if variants and not any(v in names for v in variants): continue
            if place and not any(place.casefold() in e.get('place', '').casefold() for e in p.data.get('events', [])): continue
            if year is not None:
                dates = [e['date'] for e in p.data.get('events', []) if e['type'] == 'BIRT']
                years = [int(y) for date in dates for y in re.findall(r'\b(\d{4})\b', date)]
                if not any(abs(y - year) <= spread for y in years): continue
            if relative and not any(relative.casefold() in n for n in relatives[p.id]): continue
            results.append(summary(p))
        return {'total': len(results), 'people': results[offset:offset + limit]}

    @app.get('/api/people/{id}', dependencies=[Depends(logged_in)])
    def person(id: str, s=Depends(db)):
        p = s.get(Person, id)
        if not p: raise HTTPException(404, 'Person not found.')
        relations = []
        for r in s.scalars(select(Relationship).where(or_(Relationship.parent_id == id, Relationship.child_id == id))):
            other = s.get(Person, r.child_id if r.parent_id == id else r.parent_id)
            relations.append({'id': r.id, 'parent_id': r.parent_id, 'child_id': r.child_id,
                              'role': 'Child' if r.parent_id == id else 'Parent', 'person': summary(other),
                              'status': r.status, 'evidence': r.evidence})
        tree = s.get(Import, p.import_id)
        return {**summary(p), 'data': p.data, 'relationships': relations, 'import_id': p.import_id,
                'sources': tree.parsed['sources'], 'workspace_ids': [m.workspace_id for m in s.scalars(select(Membership).where(Membership.person_id == id))]}

    @app.put('/api/people/{id}/workspaces', dependencies=[Depends(logged_in)])
    def memberships(id: str, body: MemberBody, s=Depends(db)):
        if not s.get(Person, id): raise HTTPException(404, 'Person not found.')
        # Membership is additive; notes/documents in old workspaces remain reachable.
        for wid in set(body.workspace_ids):
            workspace(s, wid)
            if not s.get(Membership, (wid, id)): s.add(Membership(workspace_id=wid, person_id=id))
        audit(s, 'person_shared_with_workspace', person_id=id, workspace_ids=body.workspace_ids)
        s.commit(); return {'ok': True}

    @app.post('/api/relationships/review', dependencies=[Depends(logged_in)])
    def relationship_review(body: RelationshipBody, s=Depends(db)):
        if not body.reviewed: raise HTTPException(400, 'Explicit relationship review is required.')
        for id in (body.parent_id, body.child_id):
            if not s.get(Person, id): raise HTTPException(404, 'Person not found.')
        if body.parent_id == body.child_id: raise HTTPException(400, 'A person cannot be their own parent.')
        graph = defaultdict(list)
        for r in s.scalars(select(Relationship)): graph[r.parent_id].append(r.child_id)
        todo, seen = [body.child_id], set()
        while todo:
            id = todo.pop()
            if id == body.parent_id: raise HTTPException(400, 'This would create an ancestry cycle.')
            if id not in seen: seen.add(id); todo.extend(graph[id])
        r = s.scalar(select(Relationship).where(Relationship.parent_id == body.parent_id, Relationship.child_id == body.child_id))
        if not r: r = Relationship(parent_id=body.parent_id, child_id=body.child_id); s.add(r)
        previous = r.status
        r.status = 'reviewed'; r.evidence = body.evidence
        audit(s, 'relationship_reviewed', parent_id=body.parent_id, child_id=body.child_id, previous=previous, evidence=body.evidence)
        s.commit(); return {'ok': True}

    @app.get('/api/entries', dependencies=[Depends(logged_in)])
    def entries(workspace_id: str, person_id: str | None = None, s=Depends(db)):
        workspace(s, workspace_id)
        query = select(Entry).where(Entry.workspace_id == workspace_id).order_by(Entry.created.desc())
        if person_id: query = query.where(Entry.person_id == person_id)
        return [{'id': e.id, 'person_id': e.person_id, 'kind': e.kind, 'title': e.title, 'text': e.text,
                 'source_url': e.source_url, 'status': e.status, 'created': e.created} for e in s.scalars(query)]

    @app.post('/api/entries', dependencies=[Depends(logged_in)])
    def add_entry(body: EntryBody, s=Depends(db)):
        if body.kind not in ('note', 'question', 'lead'): raise HTTPException(400, 'Unknown entry type.')
        linked_person(s, body.workspace_id, body.person_id); safe_url(body.source_url)
        entry = Entry(**body.model_dump()); s.add(entry); s.flush()
        audit(s, 'research_entry_added', entry_id=entry.id, workspace_id=body.workspace_id, kind=body.kind)
        s.commit(); return {'id': entry.id}

    @app.patch('/api/entries/{id}', dependencies=[Depends(logged_in)])
    def change_entry(id: str, body: EntryStatus, s=Depends(db)):
        item = s.get(Entry, id)
        if not item: raise HTTPException(404, 'Entry not found.')
        if body.status not in ('open', 'rejected', 'resolved'): raise HTTPException(400, 'Unknown status.')
        audit(s, 'research_entry_status', entry_id=id, previous=item.status, status=body.status)
        item.status = body.status; s.commit(); return {'ok': True}

    @app.post('/api/documents', dependencies=[Depends(logged_in)])
    async def upload_document(workspace_id: str = Form(), person_id: str = Form(''), description: str = Form(''), file: UploadFile = File(), s=Depends(db)):
        linked_person(s, workspace_id, person_id or None)
        name = Path((file.filename or 'document').replace('\\', '/')).name[:250]
        allowed = {'.pdf','.jpg','.jpeg','.png','.webp','.gif','.tif','.tiff','.txt'}
        if Path(name).suffix.lower() not in allowed: raise HTTPException(400, 'Upload PDF, an image or a text file.')
        if len(description) > 100000: raise HTTPException(400, 'Description is too long.')
        raw = await file.read(gedcom.MAX_FILE + 1)
        if not raw or len(raw) > gedcom.MAX_FILE: raise HTTPException(400, 'File must be between 1 byte and 25 MiB.')
        digest = hashlib.sha256(raw).hexdigest(); id = uid(); stored = f'documents/{id}.bin'
        (data_dir / stored).write_bytes(raw)
        s.add(Document(id=id, workspace_id=workspace_id, person_id=person_id or None, filename=name,
                       storage=stored, sha256=digest, size=len(raw), description=description))
        audit(s, 'document_uploaded', document_id=id, workspace_id=workspace_id, person_id=person_id or None, filename=name, sha256=digest)
        s.commit(); return {'id': id}

    @app.get('/api/documents', dependencies=[Depends(logged_in)])
    def documents(workspace_id: str, person_id: str | None = None, s=Depends(db)):
        workspace(s, workspace_id)
        query = select(Document).where(Document.workspace_id == workspace_id).order_by(Document.created.desc())
        if person_id: query = query.where(Document.person_id == person_id)
        return [{'id': d.id, 'filename': d.filename, 'person_id': d.person_id, 'description': d.description,
                 'sha256': d.sha256, 'size': d.size, 'created': d.created} for d in s.scalars(query)]

    @app.get('/api/documents/{id}/download', dependencies=[Depends(logged_in)])
    def download_document(id: str, s=Depends(db)):
        item = s.get(Document, id)
        if not item: raise HTTPException(404, 'Document not found.')
        return FileResponse(data_dir / item.storage, filename=item.filename, media_type='application/octet-stream')

    @app.get('/api/audit', dependencies=[Depends(logged_in)])
    def audit_log(offset: int = 0, s=Depends(db)):
        return [{'id': a.id, 'action': a.action, 'detail': a.detail, 'created': a.created} for a in s.scalars(select(Audit).order_by(Audit.created.desc()).offset(max(0, offset)).limit(100))]

    @app.exception_handler(IntegrityError)
    async def duplicate_handler(request, exc):
        return JSONResponse({'detail': 'A conflicting update occurred. Refresh and review before trying again.'}, status_code=409)

    if (ROOT / 'dist').is_dir(): app.mount('/', StaticFiles(directory=ROOT / 'dist', html=True), name='frontend')
    return app
