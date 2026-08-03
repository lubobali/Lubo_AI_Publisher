"""Tiny branded short-link service (Phase 2.26) — attribution without the ugly ?utm= URL.

The shortener lives entirely in the publisher: codes are generated at publish time and stored
in the publisher DB; the publisher's /go/{code} route (src/api.py) resolves a code to a 302 to
lubot.ai/?utm_... . nginx routes lubot.ai/go/ to the publisher, so the viewer sees a tiny
lubot.ai/go/<code> link while our analytics still records source + medium + campaign.

Design: ONE code per (platform, campaign, dest_path) — idempotent, since utm_campaign is already
topic+date. Stateless dest: we store the parts and rebuild the utm string at resolve time, so a
utm-format change never breaks existing codes.
"""

import secrets

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.models import PublisherShortLink
from src.post_processor import _PLATFORM_UTM  # single source of truth for platform -> (source, medium)

CODE_LEN = 5
# Unambiguous base62-ish alphabet (drop look-alikes 0/O/1/l/I) so codes are easy to read/type.
_ALPHABET = "23456789abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ"


def generate_code(n: int = CODE_LEN) -> str:
    """A random n-char short code from the unambiguous alphabet."""
    return "".join(secrets.choice(_ALPHABET) for _ in range(n))


def _utm_dest(platform: str, campaign: str, dest_path: str = "/") -> str:
    """Build the RELATIVE redirect target: /{path}?utm_source=..&utm_medium=..&utm_campaign=..

    Relative on purpose so the 302 lands on whatever host served it (lubot.ai in prod)."""
    source, medium = _PLATFORM_UTM.get(platform, (platform, "post"))
    path = "/" + dest_path.lstrip("/") if dest_path and dest_path != "/" else "/"
    return f"{path}?utm_source={source}&utm_medium={medium}&utm_campaign={campaign}"


def get_or_create_code(session: Session, *, platform: str, campaign: str, dest_path: str = "/") -> str:
    """Return the short code for this (platform, campaign, dest_path), creating it if new.

    Idempotent: the same tuple always maps to the same code (no duplicate rows). Caller commits."""
    path = dest_path or "/"
    existing = session.query(PublisherShortLink).filter_by(platform=platform, campaign=campaign, dest_path=path).first()
    if existing:
        return existing.code

    # New link — generate a unique code (retry on the rare collision).
    for _ in range(8):
        code = generate_code()
        row = PublisherShortLink(code=code, platform=platform, campaign=campaign, dest_path=path)
        session.add(row)
        try:
            session.flush()
            return code
        except IntegrityError:
            session.rollback()
            # Another row for the same tuple may have appeared concurrently — reuse it.
            dup = (
                session.query(PublisherShortLink)
                .filter_by(platform=platform, campaign=campaign, dest_path=path)
                .first()
            )
            if dup:
                return dup.code
            # else code collision — loop and try a fresh code
    raise RuntimeError("could not allocate a unique short code")


def resolve(session: Session, code: str) -> str | None:
    """Resolve a short code to its relative utm redirect target, or None if unknown."""
    row = session.query(PublisherShortLink).filter_by(code=code).first()
    if not row:
        return None
    return _utm_dest(row.platform, row.campaign, row.dest_path)
