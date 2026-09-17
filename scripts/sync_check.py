"""Phase 1B cross-platform synchronization check (the exact 10-step test).

Simulates Web and Mobile as two HTTP clients against ONE live backend and
proves there is a single authoritative student state.

Usage:
    uvicorn app.main:app --port 8000 &      # from backend/
    python scripts/sync_check.py [--base http://localhost:8000] [--email ...]
"""
import argparse
import sys
import uuid

import httpx

STEPS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    STEPS.append(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        raise SystemExit(f"SYNC CHECK FAILED at: {name} {detail}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--email", default=f"sync-{uuid.uuid4().hex[:8]}@example.com")
    args = ap.parse_args()
    B = args.base.rstrip("/") + "/api/v1"
    email, pw = args.email, "passWord123"
    web, mobile = httpx.Client(timeout=10), httpx.Client(timeout=10)

    # 1. Create account on Web.
    r = web.post(f"{B}/auth/register", json={"email": email, "password": pw})
    check("1. web register -> 201 + token pair", r.status_code == 201, r.text[:120])
    web_token = r.json()["access_token"]

    # 2. Create student profile (web onboarding equivalent).
    r = web.put(
        f"{B}/profiles/me",
        json={"full_name": "Sync Student", "hours_per_week": 6, "onboarding_completed": True},
        headers={"Authorization": f"Bearer {web_token}"},
    )
    check("2. web profile setup -> 200", r.status_code == 200, r.text[:120])
    web_profile_id = r.json()["id"]
    web.post(
        f"{B}/profiles/me/goals",
        json={"title": "Web goal"},
        headers={"Authorization": f"Bearer {web_token}"},
    )

    # 3+4. Open Mobile: login with same account, verify profile.
    r = mobile.post(f"{B}/auth/login", json={"email": email, "password": pw})
    check("3. mobile login same account -> 200", r.status_code == 200, r.text[:120])
    mobile_token = r.json()["access_token"]
    r = mobile.get(f"{B}/profiles/me", headers={"Authorization": f"Bearer {mobile_token}"})
    mp = r.json()
    check("4. mobile sees web profile", r.status_code == 200 and mp["id"] == web_profile_id)
    check("4b. mobile sees web full_name", mp["full_name"] == "Sync Student", mp["full_name"])
    goals = mobile.get(
        f"{B}/profiles/me/goals", headers={"Authorization": f"Bearer {mobile_token}"}
    ).json()
    check("4c. mobile sees web goal", any(g["title"] == "Web goal" for g in goals))

    # 5+6. Modify profile on Mobile (name + mobile-originated goal).
    r = mobile.put(
        f"{B}/profiles/me",
        json={"full_name": "Sync Student (mobile edit)", "hours_per_week": 9},
        headers={"Authorization": f"Bearer {mobile_token}"},
    )
    check("5. mobile profile edit -> 200", r.status_code == 200, r.text[:120])
    mobile.post(
        f"{B}/profiles/me/goals",
        json={"title": "Mobile goal"},
        headers={"Authorization": f"Bearer {mobile_token}"},
    )

    # 7+8. Open Web: verify modification.
    r = web.get(f"{B}/profiles/me", headers={"Authorization": f"Bearer {web_token}"})
    check("7/8. web sees mobile edit", r.json()["full_name"] == "Sync Student (mobile edit)")
    goals = web.get(
        f"{B}/profiles/me/goals", headers={"Authorization": f"Bearer {web_token}"}
    ).json()
    check("8b. web sees mobile goal", any(g["title"] == "Mobile goal" for g in goals))

    # 9+10. Modify profile on Web, verify on Mobile.
    web.put(
        f"{B}/profiles/me",
        json={"full_name": "Sync Student (web edit)"},
        headers={"Authorization": f"Bearer {web_token}"},
    )
    r = mobile.get(f"{B}/profiles/me", headers={"Authorization": f"Bearer {mobile_token}"})
    check("9/10. mobile sees web edit", r.json()["full_name"] == "Sync Student (web edit)")

    print("\n".join(STEPS))
    print("SYNC CHECK: one authoritative state confirmed across web + mobile.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
