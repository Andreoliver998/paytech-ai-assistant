from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import MembershipDB, TenantDB, UserDB
from ..settings import settings


_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
_bearer = HTTPBearer(auto_error=False)


@dataclass
class AuthContext:
    user_id: str
    tenant_id: str
    role: str
    email: str


def _jwt_secret() -> str:
    primary = (settings.JWT_SECRET.get_secret_value() or "").strip()
    legacy = (settings.JWT_SECRET_KEY.get_secret_value() or "").strip()
    secret = primary or legacy
    if secret:
        return secret
    if (settings.ENV or "").strip().lower() == "dev":
        return "paytech-dev-local-jwt-secret-change-me"
    raise RuntimeError("JWT_SECRET não configurado.")


def _jwt_algorithm() -> str:
    return (settings.JWT_ALG or settings.JWT_ALGORITHM or "HS256").strip() or "HS256"


def _jwt_expires_min() -> int:
    mins = int(settings.JWT_EXPIRES_MIN or 0)
    if mins > 0:
        return mins
    return max(60, int(settings.JWT_EXPIRE_HOURS or 12) * 60)


def hash_password(password: str) -> str:
    return _pwd_context.hash((password or "").strip())


def verify_password(plain_password: str, password_hash: str) -> bool:
    try:
        return _pwd_context.verify((plain_password or "").strip(), (password_hash or "").strip())
    except Exception:
        return False


def _normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def _slugify(s: str) -> str:
    x = (s or "").strip().lower()
    x = re.sub(r"[^\w\s-]+", "", x)
    x = re.sub(r"[\s_]+", "-", x)
    x = re.sub(r"-{2,}", "-", x).strip("-")
    return x[:160] or uuid.uuid4().hex[:8]


def create_access_token(*, user_id: str, tenant_id: str, role: str, email: str) -> str:
    now = datetime.now(timezone.utc)
    exp = now + timedelta(minutes=_jwt_expires_min())
    payload = {
        "sub": user_id,
        "user_id": user_id,
        "tenant_id": tenant_id,
        "role": role,
        "email": email,
        "iat": now,
        "exp": exp,
    }
    return jwt.encode(payload, _jwt_secret(), algorithm=_jwt_algorithm())


def register_user(db: Session, tenant_name: str, email: str, password: str) -> tuple[TenantDB, UserDB, MembershipDB]:
    t_name = (tenant_name or "").strip()
    e = _normalize_email(email)
    p = (password or "").strip()
    if not t_name or not e or not p:
        raise ValueError("tenant_name, email e password são obrigatórios.")
    if len(p) < 6:
        raise ValueError("A senha deve ter no mínimo 6 caracteres.")

    base_slug = _slugify(t_name)
    slug = base_slug
    i = 1
    while db.query(TenantDB).filter(TenantDB.slug == slug).first():
        i += 1
        slug = f"{base_slug}-{i}"

    tenant = TenantDB(name=t_name, slug=slug, status="ACTIVE")
    db.add(tenant)
    db.flush()

    exists = db.query(UserDB).filter(UserDB.tenant_id == tenant.id, UserDB.email == e).first()
    if exists:
        raise ValueError("Já existe usuário com este email para esta empresa.")

    user = UserDB(
        tenant_id=tenant.id,
        email=e,
        username=f"user-{uuid.uuid4().hex[:12]}",
        password_hash=hash_password(p),
        status="ACTIVE",
    )
    db.add(user)
    db.flush()

    membership = MembershipDB(tenant_id=tenant.id, user_id=user.id, role="OWNER")
    db.add(membership)
    db.commit()
    db.refresh(tenant)
    db.refresh(user)
    db.refresh(membership)
    return tenant, user, membership


def authenticate_user(db: Session, email: str, password: str, tenant: Optional[str] = None) -> tuple[UserDB, TenantDB, MembershipDB] | None:
    e = _normalize_email(email)
    p = (password or "").strip()
    t = (tenant or "").strip().lower()
    if not e or not p:
        return None

    q = (
        db.query(UserDB, TenantDB, MembershipDB)
        .join(MembershipDB, MembershipDB.user_id == UserDB.id)
        .join(TenantDB, TenantDB.id == MembershipDB.tenant_id)
        .filter(UserDB.email == e)
    )
    if t:
        q = q.filter((TenantDB.id == t) | (TenantDB.slug == t) | (TenantDB.name.ilike(t)))

    row = q.order_by(TenantDB.createdAt.asc()).first()
    if not row:
        return None
    user, tenant_row, membership = row
    if not verify_password(p, user.password_hash):
        return None
    return user, tenant_row, membership


def maybe_seed_demo(db: Session) -> None:
    if (settings.ENV or "").strip().lower() != "dev" or not bool(settings.SEED_DEMO):
        return
    if db.query(TenantDB).count() > 0:
        return
    register_user(db, tenant_name="Demo", email="admin@local", password="admin123")


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> AuthContext:
    if not creds or (creds.scheme or "").lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token ausente.")

    token = (creds.credentials or "").strip()
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido.")

    try:
        payload = jwt.decode(token, _jwt_secret(), algorithms=[_jwt_algorithm()])
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido.")

    user_id = str(payload.get("user_id") or payload.get("sub") or "").strip()
    tenant_id = str(payload.get("tenant_id") or "").strip()
    role = str(payload.get("role") or "MEMBER").strip().upper() or "MEMBER"
    email = _normalize_email(str(payload.get("email") or ""))
    if not user_id or not tenant_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido.")

    user = db.query(UserDB).filter(UserDB.id == user_id, UserDB.tenant_id == tenant_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Usuário não encontrado.")

    membership = (
        db.query(MembershipDB)
        .filter(MembershipDB.user_id == user_id, MembershipDB.tenant_id == tenant_id)
        .first()
    )
    if not membership:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Membro não encontrado.")

    return AuthContext(
        user_id=user.id,
        tenant_id=tenant_id,
        role=(membership.role or role or "MEMBER").upper(),
        email=email or _normalize_email(user.email or ""),
    )
