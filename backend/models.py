from sqlalchemy import String, Text, Integer, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime
import uuid
from .db import Base

def now():
    return datetime.now()

class SessionDB(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    title: Mapped[str] = mapped_column(String(200), default="Conversa")
    createdAt: Mapped[datetime] = mapped_column(DateTime, default=now)
    updatedAt: Mapped[datetime] = mapped_column(DateTime, default=now)

    messages = relationship("MessageDB", back_populates="session", cascade="all, delete-orphan")

class MessageDB(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), ForeignKey("sessions.id"), index=True)
    tenant_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    role: Mapped[str] = mapped_column(String(20))  # user/assistant/system
    content: Mapped[str] = mapped_column(Text)
    createdAt: Mapped[datetime] = mapped_column(DateTime, default=now)

    session = relationship("SessionDB", back_populates="messages")

class FileDB(Base):
    __tablename__ = "files"

    file_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    tenant_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    filename: Mapped[str] = mapped_column(String(260))
    ext: Mapped[str] = mapped_column(String(20))
    stored_path: Mapped[str] = mapped_column(String(500))
    full_text_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cols: Mapped[int | None] = mapped_column(Integer, nullable=True)
    columns_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    text_chars: Mapped[int | None] = mapped_column(Integer, nullable=True)
    size: Mapped[int] = mapped_column(Integer, default=0)
    createdAt: Mapped[datetime] = mapped_column(DateTime, default=now)

class KBChunkDB(Base):
    __tablename__ = "kb_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    file_id: Mapped[str] = mapped_column(String(200), index=True)
    filename: Mapped[str] = mapped_column(String(260))
    ext: Mapped[str] = mapped_column(String(20))
    text: Mapped[str] = mapped_column(Text)
    embedding_json: Mapped[str] = mapped_column(Text)
    createdAt: Mapped[datetime] = mapped_column(DateTime, default=now)


class DownloadFileDB(Base):
    __tablename__ = "downloads_files"

    id: Mapped[str] = mapped_column(String(200), primary_key=True)
    tenant_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    filename: Mapped[str] = mapped_column(String(260))
    ext: Mapped[str] = mapped_column(String(20))
    stored_path: Mapped[str] = mapped_column(String(500))
    full_text_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cols: Mapped[int | None] = mapped_column(Integer, nullable=True)
    columns_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    text_chars: Mapped[int | None] = mapped_column(Integer, nullable=True)
    size: Mapped[int] = mapped_column(Integer, default=0)
    createdAt: Mapped[datetime] = mapped_column(DateTime, default=now)


class DownloadChunkDB(Base):
    __tablename__ = "downloads_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    file_id: Mapped[str] = mapped_column(String(200), ForeignKey("downloads_files.id"), index=True)
    tenant_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    filename: Mapped[str] = mapped_column(String(260))
    ext: Mapped[str] = mapped_column(String(20))
    text: Mapped[str] = mapped_column(Text)
    embedding_json: Mapped[str] = mapped_column(Text)
    createdAt: Mapped[datetime] = mapped_column(DateTime, default=now)


class UserMemoryDB(Base):
    __tablename__ = "user_memory"

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    preferences_json: Mapped[str] = mapped_column(Text, default="{}")
    createdAt: Mapped[datetime] = mapped_column(DateTime, default=now)
    updatedAt: Mapped[datetime] = mapped_column(DateTime, default=now)


class UserPrefDB(Base):
    __tablename__ = "user_prefs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    key: Mapped[str] = mapped_column(String(80), index=True)
    value: Mapped[str] = mapped_column(Text, default="")
    embedding_json: Mapped[str] = mapped_column(Text, default="[]")
    updatedAt: Mapped[datetime] = mapped_column(DateTime, default=now)


class ProductPrefDB(Base):
    __tablename__ = "product_prefs"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updatedAt: Mapped[datetime] = mapped_column(DateTime, default=now)


class ThreadMetaDB(Base):
    __tablename__ = "thread_meta"

    thread_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(200), default="Conversa")
    createdAt: Mapped[datetime] = mapped_column(DateTime, default=now)
    updatedAt: Mapped[datetime] = mapped_column(DateTime, default=now)


class DownloadChunkMetaDB(Base):
    __tablename__ = "downloads_chunk_meta"

    chunk_id: Mapped[int] = mapped_column(Integer, ForeignKey("downloads_chunks.id"), primary_key=True)
    meta_json: Mapped[str] = mapped_column(Text, default="{}")
    createdAt: Mapped[datetime] = mapped_column(DateTime, default=now)


class UserDB(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("tenant_id", "email", name="uq_users_tenant_email"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid.uuid4().hex)
    tenant_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("tenants.id"), index=True, nullable=True)
    email: Mapped[str] = mapped_column(String(255), index=True, default="")
    username: Mapped[str | None] = mapped_column(String(80), index=True, nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    createdAt: Mapped[datetime] = mapped_column(DateTime, default=now)


class TenantDB(Base):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid.uuid4().hex)
    name: Mapped[str] = mapped_column(String(160))
    slug: Mapped[str | None] = mapped_column(String(160), index=True, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    createdAt: Mapped[datetime] = mapped_column(DateTime, default=now)
    updatedAt: Mapped[datetime] = mapped_column(DateTime, default=now)


class MembershipDB(Base):
    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", name="uq_memberships_tenant_user"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid.uuid4().hex)
    tenant_id: Mapped[str] = mapped_column(String(64), ForeignKey("tenants.id"), index=True)
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(20), default="MEMBER")
    createdAt: Mapped[datetime] = mapped_column(DateTime, default=now)


class PlanDB(Base):
    __tablename__ = "plans"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid.uuid4().hex)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    limits_json: Mapped[str] = mapped_column(Text, default="{}")
    createdAt: Mapped[datetime] = mapped_column(DateTime, default=now)


class AuditLogDB(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid.uuid4().hex)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    actor_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action: Mapped[str] = mapped_column(String(120), index=True)
    meta_json: Mapped[str] = mapped_column(Text, default="{}")
    createdAt: Mapped[datetime] = mapped_column(DateTime, default=now)
