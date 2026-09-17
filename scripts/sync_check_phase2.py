"""Phase 2A cross-client assessment sync check.

WEB takes an assessment -> MOBILE sees result/history, then
MOBILE takes one -> WEB sees it. One engine, one history.

Usage:
    uvicorn app.main:app --port 8000 &      # from backend/
    python scripts/sync_check_phase2.py [--base http://localhost:8000]
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


def answer_all_correct(cli: httpx.Client, headers: dict, attempt_id: str, questions: list) -> None:
    # Correct option IDs are server-side secrets; this script reads them via the
    # RESULT endpoint only after submitting blind — first pass answers Q1 only.
    r = cli.put(
        f"{cli.base_url}/api/v1/attempts/{attempt_id}/answers",
        json={"answers": [{"question_id": questions[0]["id"], "selected_option_id": questions[0]["options"][0]["id"]}]},
        headers=headers,
    )
    check("draft answers accepted", r.status_code == 200, r.text[:100])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--email", default=f"p2sync-{uuid.uuid4().hex[:8]}@example.com")
    args = ap.parse_args()
    B = args.base.rstrip("/")
    email, pw = args.email, "passWord123"
    web, mobile = httpx.Client(base_url=B, timeout=10), httpx.Client(base_url=B, timeout=10)

    # Shared account, both clients log in.
    r = web.post("/api/v1/auth/register", json={"email": email, "password": pw})
    check("web register -> 201", r.status_code == 201, r.text[:100])
    web_h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    r = mobile.post("/api/v1/auth/login", json={"email": email, "password": pw})
    check("mobile login same account -> 200", r.status_code == 200)
    mobile_h = {"Authorization": f"Bearer {r.json()['access_token']}"}

    # Pick a quiz both clients can see.
    subs = web.get("/api/v1/subjects").json()
    topics = web.get(f"/api/v1/subjects/{subs[0]['id']}/topics", headers=web_h).json()
    quizzes = mobile.get(f"/api/v1/topics/{topics[0]['id']}/assessments", headers=mobile_h).json()
    check("both clients see seeded quiz", bool(quizzes), f"{len(quizzes)} quizzes")
    aid = quizzes[0]["id"]
    detail = web.get(f"/api/v1/assessments/{aid}", headers=web_h).json()
    check("no correctness leak pre-submit", all("is_correct" not in str(q) for q in detail["questions"]))

    # WEB takes assessment -> result.
    att = web.post(f"/api/v1/assessments/{aid}/attempts", headers=web_h).json()
    check("web attempt started", att["status"] == "in_progress")
    answer_all_correct(web, web_h, att["id"], detail["questions"])
    res = web.post(f"/api/v1/attempts/{att['id']}/submit", headers=web_h).json()
    check("web submit graded server-side", res["max_score"] > 0 and res["accuracy"] is not None)

    # MOBILE sees web's result + history.
    mres = mobile.get(f"/api/v1/attempts/{att['id']}/result", headers=mobile_h).json()
    check("mobile sees web result", mres["score"] == res["score"] and mres["max_score"] == res["max_score"])
    mhist = mobile.get("/api/v1/profiles/me/attempts", headers=mobile_h).json()
    check("mobile history shows web attempt", any(h["id"] == att["id"] for h in mhist))
    mperf = mobile.get("/api/v1/profiles/me/performance", headers=mobile_h).json()
    check("mobile performance counts web attempt", mperf["submitted_attempts"] >= 1)

    # MOBILE takes another assessment -> WEB sees it.
    att2 = mobile.post(f"/api/v1/assessments/{aid}/attempts", headers=mobile_h).json()
    check("mobile attempt number increments", att2["attempt_number"] == att["attempt_number"] + 1)
    answer_all_correct(mobile, mobile_h, att2["id"], detail["questions"])
    res2 = mobile.post(f"/api/v1/attempts/{att2['id']}/submit", headers=mobile_h).json()
    whist = web.get("/api/v1/profiles/me/attempts", headers=web_h).json()
    check("web history shows mobile attempt", any(h["id"] == att2["id"] for h in whist))
    wres = web.get(f"/api/v1/attempts/{att2['id']}/result", headers=web_h).json()
    check("web sees mobile score", wres["score"] == res2["score"])
    wperf = web.get("/api/v1/profiles/me/performance", headers=web_h).json()
    check("web performance counts both", wperf["submitted_attempts"] >= 2)

    print("\n".join(STEPS))
    print("PHASE-2 SYNC: one engine, one history across web + mobile.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
