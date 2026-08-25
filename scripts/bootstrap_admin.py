#!/usr/bin/env python3
"""
Create the first administrator on a production install.

Demo accounts are seeded only outside production, so a fresh production
database has no users at all and nobody can sign in. This is the deliberate
first-run step that fixes that (spec 57).

Usage
    docker compose -f docker-compose.prod.yml exec api \\
        python scripts/bootstrap_admin.py --email you@company.com

The password is read from the ADMIN_PASSWORD environment variable, or prompted
for interactively. It is never accepted on the command line, where it would be
captured by shell history and by `ps`.
"""

from __future__ import annotations

import argparse
import getpass
import os
import re
import secrets
import string
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "apps", "api"))
sys.path.insert(0, "/app")  # inside the container the app lives here

MIN_LENGTH = 12


def strength_problems(password: str) -> list[str]:
    problems = []
    if len(password) < MIN_LENGTH:
        problems.append(f"must be at least {MIN_LENGTH} characters")
    if not re.search(r"[a-z]", password):
        problems.append("needs a lowercase letter")
    if not re.search(r"[A-Z]", password):
        problems.append("needs an uppercase letter")
    if not re.search(r"\d", password):
        problems.append("needs a digit")
    if password.lower() in {"password", "admin", "demo1234", "changeme"}:
        problems.append("is a well-known password")
    return problems


def suggest() -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*-_"
    return "".join(secrets.choice(alphabet) for _ in range(20))


def main() -> int:
    parser = argparse.ArgumentParser(description="Create the first administrator.")
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", default=None, help="Full name (defaults to the email local part)")
    parser.add_argument(
        "--generate-password",
        action="store_true",
        help="Generate a strong password and print it once",
    )
    parser.add_argument(
        "--promote",
        action="store_true",
        help="If the account already exists, raise it to Super Admin",
    )
    args = parser.parse_args()

    from sqlalchemy import func, select

    from app.core.db import init_database, session_scope
    from app.core.security import hash_password
    from app.models.metadata_models import Role, User

    # Create tables if this is a brand new install, but never seed demo users.
    init_database(seed_dev_users=False)

    generated = None
    if args.generate_password:
        generated = suggest()
        password = generated
    else:
        password = os.environ.get("ADMIN_PASSWORD") or ""
        if not password:
            if not sys.stdin.isatty():
                print(
                    "No password supplied. Set ADMIN_PASSWORD, or use "
                    "--generate-password, or run this interactively.",
                    file=sys.stderr,
                )
                return 2
            password = getpass.getpass("Password for the new administrator: ")
            if password != getpass.getpass("Confirm password: "):
                print("Passwords did not match.", file=sys.stderr)
                return 2

    problems = strength_problems(password)
    if problems:
        print("That password " + "; ".join(problems) + ".", file=sys.stderr)
        print(f"\nA strong example: {suggest()}", file=sys.stderr)
        return 2

    email = args.email.strip().lower()

    with session_scope() as session:
        existing = session.scalar(select(User).where(User.email == email))
        if existing is not None:
            if not args.promote:
                print(
                    f"An account for {email} already exists. Re-run with --promote "
                    "to reset its password and make it a Super Admin.",
                    file=sys.stderr,
                )
                return 1
            existing.password_hash = hash_password(password)
            existing.role = Role.SUPER_ADMIN
            existing.is_active = True
            action = "updated"
        else:
            session.add(
                User(
                    email=email,
                    full_name=args.name or email.split("@")[0].replace(".", " ").title(),
                    role=Role.SUPER_ADMIN,
                    password_hash=hash_password(password),
                    is_active=True,
                )
            )
            action = "created"

        total = session.scalar(select(func.count()).select_from(User)) or 0

    print(f"\nSuper Admin {action}: {email}")
    if generated:
        print(f"Password (shown once, store it in your password manager):\n\n    {generated}\n")
    print(f"Accounts in this installation: {total + (1 if action == 'created' else 0)}")
    print("\nSign in, then create the remaining users and assign their roles.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
