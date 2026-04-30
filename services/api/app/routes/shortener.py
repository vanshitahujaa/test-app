"""Real business endpoints: shorten URL, redirect, stats."""

import secrets
import logging
from urllib.parse import urlparse
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, HttpUrl
from sqlalchemy import select

from ..db import SessionLocal, Link
from ..cache import cache_get, cache_set, emit_click
from ..metrics import shortener_links_total, shortener_clicks_total

logger = logging.getLogger("api.shortener")
router = APIRouter()


class ShortenRequest(BaseModel):
    url: HttpUrl


class ShortenResponse(BaseModel):
    code: str
    short_url: str
    target_url: str


def _make_code(n: int = 7) -> str:
    return secrets.token_urlsafe(n)[:n]


@router.post("/shorten", response_model=ShortenResponse, status_code=201)
def shorten(req: ShortenRequest):
    target = str(req.url)
    if not urlparse(target).scheme.startswith("http"):
        raise HTTPException(400, "url must be http or https")

    with SessionLocal() as db:
        for _ in range(5):
            code = _make_code()
            existing = db.execute(select(Link).where(Link.code == code)).scalar_one_or_none()
            if existing is None:
                db.add(Link(code=code, target_url=target))
                db.commit()
                shortener_links_total.inc()
                cache_set(code, target)
                return ShortenResponse(code=code, short_url=f"/{code}", target_url=target)
        raise HTTPException(500, "could not allocate a unique code")


@router.get("/{code}")
def redirect(code: str):
    cached = cache_get(code)
    if cached is not None:
        shortener_clicks_total.labels(hit="cache_hit").inc()
        emit_click(code)
        return Response(status_code=307, headers={"Location": cached})

    with SessionLocal() as db:
        link = db.execute(select(Link).where(Link.code == code)).scalar_one_or_none()
        if link is None:
            raise HTTPException(404, "no such code")
        cache_set(code, link.target_url)
        shortener_clicks_total.labels(hit="cache_miss").inc()
        emit_click(code)
        return Response(status_code=307, headers={"Location": link.target_url})


@router.get("/stats/{code}")
def stats(code: str):
    with SessionLocal() as db:
        link = db.execute(select(Link).where(Link.code == code)).scalar_one_or_none()
        if link is None:
            raise HTTPException(404, "no such code")
        return {
            "code": link.code,
            "target_url": link.target_url,
            "clicks": link.clicks,
            "created_at": link.created_at.isoformat(),
        }
