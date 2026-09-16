"""
Development-only seed script.
Creates (or migrates) the demo account if needed.

NEVER run this in production — it creates an account with a known password.

Usage (from apps/api/):
    python seed_dev.py

Or from the repo root:
    python apps/api/seed_dev.py
"""

import sys
import os

# ---------------------------------------------------------------------------
# Guard: refuse to run if DATABASE_URL points to a likely production host.
# ---------------------------------------------------------------------------
_db_url = os.environ.get("DATABASE_URL", "")
_production_indicators = ["rds.amazonaws.com", "supabase", "neon.tech", "render.com"]
for _indicator in _production_indicators:
    if _indicator in _db_url:
        print(
            f"[seed_dev] ERROR: DATABASE_URL looks like a production database "
            f"({_indicator} detected). Refusing to seed.",
            file=sys.stderr,
        )
        sys.exit(1)

# ---------------------------------------------------------------------------
# Make sure Python can find the app package whether the script is run from
# apps/api/ or from the repo root.
# ---------------------------------------------------------------------------
_script_dir = os.path.dirname(os.path.abspath(__file__))
if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)

# ---------------------------------------------------------------------------
# Demo credentials — plaintext only here, never stored.
# .example TLD is reserved for documentation/testing (RFC 2606) and is
# accepted by email-validator / Pydantic EmailStr.
# ---------------------------------------------------------------------------
OLD_DEMO_EMAIL = "demo@codelens.local"   # old email — rejected by EmailStr
DEMO_EMAIL     = "demo@codelens.example"  # new canonical email
DEMO_NAME      = "CodeLens Demo"
DEMO_PASSWORD  = "Demo@12345"             # hashed via Argon2 before DB insert


def main() -> None:
    from sqlalchemy import select

    from app.core.security import get_password_hash
    from app.db.database import SessionLocal
    from app.models.user import User

    db = SessionLocal()
    try:
        # --- 1. Check whether the correct email already exists --------------
        target = db.scalar(select(User).where(User.email == DEMO_EMAIL))
        if target:
            print(
                f"[seed_dev] Demo user already exists "
                f"(id={target.id}, email={target.email!r}). Nothing to do."
            )
            return

        # --- 2. Migrate old .local row if present ---------------------------
        old = db.scalar(select(User).where(User.email == OLD_DEMO_EMAIL))
        if old:
            print(
                f"[seed_dev] Found old demo user (id={old.id}, "
                f"email={old.email!r}). Updating email to {DEMO_EMAIL!r} ..."
            )
            old.email = DEMO_EMAIL
            old.name = DEMO_NAME
            # Re-hash so the password is a valid Argon2 hash
            # (the old row from the first failed seed may have had hash="x")
            old.password_hash = get_password_hash(DEMO_PASSWORD)
            db.commit()
            db.refresh(old)
            print(
                f"[seed_dev] OK  Demo user updated.\n"
                f"           id    : {old.id}\n"
                f"           email : {old.email}\n"
                f"           name  : {old.name}\n"
                f"           Login : http://localhost:3000/login\n"
                f"           Email : {DEMO_EMAIL}\n"
                f"           Pass  : {DEMO_PASSWORD}"
            )
            return

        # --- 3. Create brand-new demo user ----------------------------------
        demo_user = User(
            email=DEMO_EMAIL,
            name=DEMO_NAME,
            password_hash=get_password_hash(DEMO_PASSWORD),
        )
        db.add(demo_user)
        db.commit()
        db.refresh(demo_user)

        print(
            f"[seed_dev] OK  Demo user created successfully.\n"
            f"           id    : {demo_user.id}\n"
            f"           email : {demo_user.email}\n"
            f"           name  : {demo_user.name}\n"
            f"           Login : http://localhost:3000/login\n"
            f"           Email : {DEMO_EMAIL}\n"
            f"           Pass  : {DEMO_PASSWORD}"
        )

    except Exception as exc:
        db.rollback()
        print(f"[seed_dev] ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
