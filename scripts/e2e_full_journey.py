"""Phase 5B end-to-end journey: the complete student life across both clients.

WEB (cookie transport, like the real web app) 1-11, then MOBILE (bearer
transport, like the real mobile app) 12-22, then the critical flow in reverse.
One backend, one truth throughout.

Usage:
    uvicorn app.main:app --port 8000 &      # from backend/
    python scripts/e2e_full_journey.py [--base http://localhost:8000]
"""
import argparse
import http.cookiejar
import sys
import urllib.request
import json
import uuid

STEPS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    STEPS.append(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        raise SystemExit(f"E2E FAILED at: {name} {detail}")


class Web:
    """Cookie-jar client: behaves like the browser app (no auth headers)."""

    def __init__(self, base: str):
        self.base = base
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(self, method, path, body=None):
        req = urllib.request.Request(
            self.base + path, method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json"},
        )
        try:
            with self.op.open(req) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")


class Mobile:
    """Bearer-header client: behaves like the mobile app."""

    def __init__(self, base: str):
        self.base = base
        self.token: str | None = None

    def call(self, method, path, body=None):
        req = urllib.request.Request(
            self.base + path, method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json",
                     **({"Authorization": f"Bearer {self.token}"} if self.token else {})},
        )
        try:
            with urllib.request.urlopen(req) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")


def answer_all(cli, att_id, questions, idx: int):
    return cli.call("PUT", f"/api/v1/attempts/{att_id}/answers",
                    {"answers": [{"question_id": q["id"], "selected_option_id": q["options"][idx]["id"]} for q in questions]})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--email", default=f"e2e-{uuid.uuid4().hex[:8]}@example.com")
    args = ap.parse_args()
    B = args.base.rstrip("/")
    email, pw = args.email, "passWord123"
    web, mob = Web(B), Mobile(B)

    # 1-4. Register, onboard, profile, subjects/skills (WEB).
    s, reg = web.call("POST", "/api/v1/auth/register", {"email": email, "password": pw})
    check("1. register on web", s == 201, str(s))
    s, me = web.call("GET", "/api/v1/auth/me")
    check("1b. session cookie authenticates", s == 200 and me["email"] == email)
    s, prof = web.call("PUT", "/api/v1/profiles/me",
                       {"full_name": "E2E Student", "hours_per_week": 7, "onboarding_completed": True})
    check("2+3. onboarding + profile", s == 200 and prof["full_name"] == "E2E Student")
    s, subs = web.call("GET", "/api/v1/subjects")
    s, _ = web.call("PUT", "/api/v1/profiles/me/subjects", {"subject_ids": [subs[0]["id"], subs[1]["id"]]})
    s, _ = web.call("POST", "/api/v1/profiles/me/skills", {"name": "Python", "level": "beginner"})
    check("4. subjects + skills selected", s == 201, str(s))

    # 5. Learn (WEB): topics -> content -> complete a lesson.
    s, topics = web.call("GET", f"/api/v1/subjects/{subs[0]['id']}/topics")
    s, content = web.call("GET", f"/api/v1/topics/{topics[0]['id']}/content")
    check("5a. browse topics + lessons", bool(topics) and bool(content))
    s, _ = web.call("PUT", f"/api/v1/profiles/me/progress/{content[0]['id']}", {"status": "completed"})
    check("5b. lesson completed", s == 200, str(s))

    # 6-7. Take + submit (WEB).
    s, quizzes = web.call("GET", f"/api/v1/topics/{topics[0]['id']}/assessments")
    aid = quizzes[0]["id"]
    s, det = web.call("GET", f"/api/v1/assessments/{aid}")
    check("6a. assessment loads without answer keys", s == 200 and all("is_correct" not in str(q) for q in det["questions"]))
    s, att = web.call("POST", f"/api/v1/assessments/{aid}/attempts", {})
    check("6b. attempt started", s in (200, 201) and att["status"] == "in_progress")
    s, _ = answer_all(web, att["id"], det["questions"], 0)
    s, res = web.call("POST", f"/api/v1/attempts/{att['id']}/submit", {})
    check("7. submitted + graded server-side", s == 200 and res["max_score"] > 0, str(res)[:80])

    # 8-11. Performance, twin, AI, recommendation (WEB).
    s, hist = web.call("GET", "/api/v1/profiles/me/attempts")
    check("8. performance record present", any(h["id"] == att["id"] for h in hist))
    s, tw = web.call("GET", "/api/v1/profiles/me/twin")
    # V2-C1: version 2 = step-4 skill-link snapshot + this submit snapshot.
    check("9. twin updated from evidence", tw["has_evidence"] and tw["version"] == 2 and tw["overall_accuracy"] == res["accuracy"])
    s, ins = web.call("GET", "/api/v1/profiles/me/insights")
    s, pred = web.call("GET", "/api/v1/profiles/me/prediction")
    check("10. AI analysis generated", isinstance(ins, list) and pred["status"] in ("ready", "insufficient_data"))
    s, plan = web.call("GET", "/api/v1/profiles/me/recommendations")
    check("11. recommendation generated", bool(plan["today"]), str(plan)[:80])

    # 12-13. MOBILE login, same profile.
    s, login = mob.call("POST", "/api/v1/auth/login", {"email": email, "password": pw})
    mob.token = login["access_token"]
    check("12+13. mobile login, same account", s == 200, str(s))
    s, mprof = mob.call("GET", "/api/v1/profiles/me")
    check("14. same profile on mobile", mprof["id"] == prof["id"] and mprof["full_name"] == "E2E Student")

    # 14-17. Same performance / twin / insights on MOBILE.
    s, mhist = mob.call("GET", "/api/v1/profiles/me/attempts")
    check("15. same performance on mobile", any(h["id"] == att["id"] for h in mhist))
    s, mtw = mob.call("GET", "/api/v1/profiles/me/twin")
    check("16. same twin on mobile", mtw["overall_mastery"] == tw["overall_mastery"] and mtw["version"] == 2)
    s, mins = mob.call("GET", "/api/v1/profiles/me/insights")
    check("17. same AI insights on mobile", len(mins) == len(ins))

    # 18-19. Follow recommendation on MOBILE: top rec's topic -> its quiz -> submit.
    top = plan["today"][0]
    check("18a. top recommendation actionable", bool(top.get("reason")))
    tid = (top.get("refs") or {}).get("topic_id")
    if tid:
        s, mquiz = mob.call("GET", f"/api/v1/topics/{tid}/assessments")
        follow_aid = mquiz[0]["id"] if mquiz else aid
    else:
        follow_aid = aid
    s, mdet = mob.call("GET", f"/api/v1/assessments/{follow_aid}")
    s, att2 = mob.call("POST", f"/api/v1/assessments/{follow_aid}/attempts", {})
    s, _ = answer_all(mob, att2["id"], mdet["questions"], -1)
    s, res2 = mob.call("POST", f"/api/v1/attempts/{att2['id']}/submit", {})
    check("18+19. followed recommendation, mobile submit graded", s == 200, str(res2)[:80])

    # 20-22. Twin changed; WEB sees everything.
    s, mtw2 = mob.call("GET", "/api/v1/profiles/me/twin")
    check("20. twin changed after mobile attempt", mtw2["version"] == 3 and mtw2["overall_mastery"] != mtw["overall_mastery"])
    s, wtw = web.call("GET", "/api/v1/profiles/me/twin")
    check("21+22a. web sees updated twin", wtw["version"] == 3 and wtw["overall_mastery"] == mtw2["overall_mastery"])
    s, whist = web.call("GET", "/api/v1/profiles/me/attempts")
    check("21+22b. web sees mobile attempt in history", any(h["id"] == att2["id"] for h in whist))
    s, wnotes = web.call("GET", "/api/v1/profiles/me/notifications")
    check("21+22c. web sees notifications", isinstance(wnotes, list) and len(wnotes) >= 1)

    # REVERSE: critical flow beginning from MOBILE with a fresh student.
    email2 = f"e2e-rev-{uuid.uuid4().hex[:8]}@example.com"
    s, reg2 = mob.call("POST", "/api/v1/auth/register", {"email": email2, "password": pw})
    mob.token = reg2["access_token"]
    check("R1. register on mobile", s == 201, str(s))
    s, _ = mob.call("PUT", "/api/v1/profiles/me", {"full_name": "Reverse Student", "onboarding_completed": True})
    check("R2. mobile profile setup", s == 200, str(s))
    s, subs2 = mob.call("GET", "/api/v1/subjects")
    s, topics2 = mob.call("GET", f"/api/v1/subjects/{subs2[0]['id']}/topics")
    s, quizzes2 = mob.call("GET", f"/api/v1/topics/{topics2[0]['id']}/assessments")
    s, det2 = mob.call("GET", f"/api/v1/assessments/{quizzes2[0]['id']}")
    s, ratt = mob.call("POST", f"/api/v1/assessments/{quizzes2[0]['id']}/attempts", {})
    s, _ = answer_all(mob, ratt["id"], det2["questions"], 0)
    s, rres = mob.call("POST", f"/api/v1/attempts/{ratt['id']}/submit", {})
    check("R3. mobile quiz + submit", s == 200, str(rres)[:60])
    s, rtw = mob.call("GET", "/api/v1/profiles/me/twin")
    check("R4. mobile twin built", rtw["has_evidence"] and rtw["version"] == 1)
    # WEB verifies the mobile-created student end to end.
    web2 = Web(B)
    s, _ = web2.call("POST", "/api/v1/auth/login", {"email": email2, "password": pw})
    check("R5. web login to mobile-created account", s in (200, 201, 204), str(s))
    s, wprof = web2.call("GET", "/api/v1/profiles/me/twin")
    check("R6. web sees mobile twin + history", wprof["overall_mastery"] == rtw["overall_mastery"])
    s, whist2 = web2.call("GET", "/api/v1/profiles/me/attempts")
    check("R7. web sees mobile attempt", any(h["id"] == ratt["id"] for h in whist2))

    print("\n".join(STEPS))
    print("E2E: full 22-step journey + reverse critical flow — one truth, both clients.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
