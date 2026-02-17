from sqlalchemy import String, Text, Integer, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime
from .db import Base

def now():
    return datetime.now()

class SessionDB(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(200), default="Conversa")
    createdAt: Mapped[datetime] = mapped_column(DateTime, default=now)
    updatedAt: Mapped[datetime] = mapped_column(DateTime, default=now)

    messages = relationship("MessageDB", back_populates="session", cascade="all, delete-orphan")

class MessageDB(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), ForeignKey("sessions.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))  # user/assistant/system
    content: Mapped[str] = mapped_column(Text)
    createdAt: Mapped[datetime] = mapped_column(DateTime, default=now)

    session = relationship("SessionDB", back_populates="messages")

class FileDB(Base):
    __tablename__ = "files"

    file_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    filename: Mapped[str] = mapped_column(String(260))
    ext: Mapped[str] = mapped_column(String(20))
    stored_path: Mapped[str] = mapped_column(String(500))
    size: Mapped[int] = mapped_column(Integer, default=0)
    createdAt: Mapped[datetime] = mapped_column(DateTime, default=now)

class KBChunkDB(Base):
    __tablename__ = "kb_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    file_id: Mapped[str] = mapped_column(String(200), index=True)
    filename: Mapped[str] = mapped_column(String(260))
    ext: Mapped[str] = mapped_column(String(20))
    text: Mapped[str] = mapped_column(Text)
    embedding_json: Mapped[str] = mapped_column(Text)
    createdAt: Mapped[datetime] = mapped_column(DateTime, default=now)


class DownloadFileDB(Base):
    __tablename__ = "downloads_files"

    id: Mapped[str] = mapped_column(String(200), primary_key=True)
    filename: Mapped[str] = mapped_column(String(260))
    ext: Mapped[str] = mapped_column(String(20))
    stored_path: Mapped[str] = mapped_column(String(500))
    size: Mapped[int] = mapped_column(Integer, default=0)
    createdAt: Mapped[datetime] = mapped_column(DateTime, default=now)


class DownloadChunkDB(Base):
    __tablename__ = "downloads_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    file_id: Mapped[str] = mapped_column(String(200), ForeignKey("downloads_files.id"), index=True)
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
