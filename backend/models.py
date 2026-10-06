import datetime as dt
import uuid
from sqlalchemy import String, Text, JSON, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def uid(): return str(uuid.uuid4())
def now(): return dt.datetime.now(dt.timezone.utc).isoformat()

class Base(DeclarativeBase): pass
class Workspace(Base):
    __tablename__ = 'workspaces'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(160), unique=True)
class Import(Base):
    __tablename__ = 'imports'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    filename: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    storage: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default='preview')
    parsed: Mapped[dict] = mapped_column(JSON)
    created: Mapped[str] = mapped_column(String(50), default=now)
class Person(Base):
    __tablename__ = 'people'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    import_id: Mapped[str] = mapped_column(ForeignKey('imports.id'))
    xref: Mapped[str] = mapped_column(String(160))
    name: Mapped[str] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30), default='imported_unverified')
    __table_args__ = (UniqueConstraint('import_id', 'xref'),)
class Membership(Base):
    __tablename__ = 'memberships'
    workspace_id: Mapped[str] = mapped_column(ForeignKey('workspaces.id'), primary_key=True)
    person_id: Mapped[str] = mapped_column(ForeignKey('people.id'), primary_key=True)
class Relationship(Base):
    __tablename__ = 'relationships'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    parent_id: Mapped[str] = mapped_column(ForeignKey('people.id'))
    child_id: Mapped[str] = mapped_column(ForeignKey('people.id'))
    status: Mapped[str] = mapped_column(String(30), default='imported_unverified')
    evidence: Mapped[str] = mapped_column(Text, default='')
    __table_args__ = (UniqueConstraint('parent_id', 'child_id'),)
class Entry(Base):
    __tablename__ = 'entries'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey('workspaces.id'))
    person_id: Mapped[str | None] = mapped_column(ForeignKey('people.id'), nullable=True)
    kind: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(300))
    text: Mapped[str] = mapped_column(Text, default='')
    source_url: Mapped[str] = mapped_column(Text, default='')
    status: Mapped[str] = mapped_column(String(20), default='open')
    created: Mapped[str] = mapped_column(String(50), default=now)
class Document(Base):
    __tablename__ = 'documents'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey('workspaces.id'))
    person_id: Mapped[str | None] = mapped_column(ForeignKey('people.id'), nullable=True)
    filename: Mapped[str] = mapped_column(Text)
    storage: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(Text, default='')
    created: Mapped[str] = mapped_column(String(50), default=now)
class Audit(Base):
    __tablename__ = 'audit'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    action: Mapped[str] = mapped_column(String(80))
    detail: Mapped[dict] = mapped_column(JSON)
    created: Mapped[str] = mapped_column(String(50), default=now)
class LoginSession(Base):
    __tablename__ = 'sessions'
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    expires: Mapped[str] = mapped_column(String(50))
