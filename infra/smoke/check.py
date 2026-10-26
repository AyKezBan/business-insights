#!/usr/bin/env python3
"""Exercise a disposable synthetic stack. Never point this at shared/live data."""
import argparse
import json
import os
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4


class SmokeFailure(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise SmokeFailure(message)


def request(base, path, *, method="GET", body=None, token=None, key=None, expected=200):
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if key:
        headers["Idempotency-Key"] = key
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = Request(base.rstrip("/") + path, data=data, method=method, headers=headers)
    try:
        response = urlopen(req, timeout=10)
    except HTTPError as error:
        response = error
    with response:
        payload = response.read()
        status = response.status
        content_type = response.headers.get("Content-Type", "")
        result = json.loads(payload) if payload and "json" in content_type else payload.decode()
        require(status == expected, f"{method} {path}: expected HTTP {expected}, got {status}: {str(result)[:500]}")
        return result, response.headers


def eventually(action, *, timeout, label):
    deadline = time.monotonic() + timeout
    last = "not ready"
    while time.monotonic() < deadline:
        try:
            result = action()
            if result:
                return result
        except (URLError, TimeoutError, OSError, SmokeFailure) as error:
            last = str(error)
        time.sleep(0.5)
    raise SmokeFailure(f"Timed out waiting for {label}: {last}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.getenv("SMOKE_BASE_URL", "http://localhost:8088"))
    parser.add_argument("--ml-url", default=os.getenv("SMOKE_ML_URL", "http://localhost:8000"))
    parser.add_argument("--backend-url", help="Direct backend URL; base-url then denotes the localhost demo gateway")
    parser.add_argument("--fixtures-dir", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--skip-ui", action="store_true", help="Check a direct backend URL without frontend HTML")
    args = parser.parse_args()
    token = os.getenv("BACKEND_DEMO_TOKEN")
    require(bool(token), "Set BACKEND_DEMO_TOKEN for this disposable stack")
    marker = json.loads((args.fixtures_dir / "synthetic-smoke.json").read_text())
    require(marker.get("kind") == "business-insights-synthetic-smoke", "Fixture directory is not a generated smoke dataset")

    def api(path, **kwargs):
        return request(args.base_url, "/api/v1" + path, token=token, **kwargs)

    # Check the explicit test-app marker before any mutating request.
    fixture_health = eventually(lambda: request(args.ml_url, "/smoke/health")[0], timeout=args.timeout, label="synthetic ML")
    require(fixture_health.get("mode") == "synthetic-scripted" and fixture_health.get("external_llm_calls") == 0,
            "ML is not the isolated scripted smoke app; refusing mutations")
    ml_health = request(args.ml_url, "/health")[0]
    require(ml_health["bank_fragments"] == 2 and ml_health["news"] == 1, "Synthetic ML corpus was not loaded")
    require(ml_health["corpus_revision"] is None, "Smoke app must not load the live material corpus")
    session = eventually(lambda: api("/session")[0], timeout=args.timeout, label="backend session")
    require(session["personId"] == "F_1", "Smoke stack must use synthetic person F_1")
    if args.backend_url:
        # The localhost demo gateway injects its configured token. The backend
        # itself must still reject unauthenticated requests on the private network.
        anonymous_gateway = request(args.base_url, "/api/v1/session")[0]
        require(anonymous_gateway["personId"] == "F_1", "Demo gateway did not establish its configured session")
        request(args.backend_url, "/api/v1/session", expected=401)
        direct_session = request(args.backend_url, "/api/v1/session", token=token)[0]
        require(direct_session["personId"] == "F_1", "Direct backend bearer authentication failed")
        require(request(args.backend_url, "/health")[0]["scope"] == "process", "Backend process health missing")
    else:
        request(args.base_url, "/api/v1/session", expected=401)
    companies = eventually(lambda: api("/companies")[0], timeout=args.timeout, label="imported companies")
    require({c["companyId"] for c in companies} == {"U_1", "U_2", "U_9"}, "Unexpected company data; refusing mutations")
    profile = api("/companies/U_1/profile")[0]
    require(profile["facts"]["revenueRub"] is None and profile["facts"]["revenueYear"] == 2025,
            "Synthetic placeholder revenue/import metadata mismatch")
    require(profile["sufficientForAnalysis"], "Synthetic U_1 profile is not usable")
    if not args.skip_ui:
        html, headers = request(args.base_url, "/")
        require("text/html" in headers.get("Content-Type", "") and "<html" in html.lower(), "Frontend HTML unavailable")

    event = json.loads((args.fixtures_dir / "backend-event.json").read_text())
    require(event["eventId"] == "evt_demo_rate" and "SYNTHETIC" in event["title"], "Expected labelled synthetic event")
    api("/events", method="POST", body=event)
    analysis = {"companyId": "U_1", "role": "cfo", "eventId": event["eventId"], "userProvided": {}}
    key = "smoke-analysis-" + uuid4().hex
    operation, headers = api("/analyses", method="POST", body=analysis, key=key, expected=202)
    require(headers.get("Location", "").endswith(operation["operationId"]) and headers.get("Retry-After"), "Async operation headers missing")
    duplicate = api("/analyses", method="POST", body=analysis, key=key, expected=202)[0]
    require(duplicate["operationId"] == operation["operationId"], "Idempotent analysis created another operation")

    def completed(operation_id):
        result = api("/operations/" + operation_id)[0]
        if result["status"] in ("QUEUED", "RUNNING"):
            return None
        require(result["status"] in ("SUCCEEDED", "LIMITED"), f"Operation failed: {result}")
        return result

    operation = eventually(lambda: completed(operation["operationId"]), timeout=args.timeout, label="analysis")
    insight_id = operation["insightId"]
    insight = api("/insights/" + insight_id)[0]
    require(insight["companyId"] == "U_1" and insight["role"] == "cfo" and insight["eventId"] == "evt_demo_rate", "Insight identity mismatch")
    require(insight["mlStatus"] in ("complete", "limited") and "SYNTHETIC" in insight["headline"], "Unexpected ML result")
    require(insight["selfCheck"]["passed"], "ML evidence validation failed")
    require("products.loans" in insight["profileFeaturesUsed"], "Company profile did not reach the ML result")
    require(any(source["kind"] == "bank" for source in insight["sources"]), "Synthetic bank evidence missing")

    def feed(include_hidden=False):
        return api("/companies/U_1/insights?role=cfo&limit=100&includeHidden=" + str(include_hidden).lower())[0]["items"]

    require(any(item["insightId"] == insight_id for item in feed()), "Generated insight absent from feed")
    api(f"/insights/{insight_id}/opened", method="POST", expected=204)
    try:
        api(f"/insights/{insight_id}/state", method="PUT", body={"hidden": True}, expected=204)
        require(api("/insights/" + insight_id)[0]["hidden"], "Hidden state was not saved")
        require(all(item["insightId"] != insight_id for item in feed()), "Hidden insight still visible in feed")
        require(any(item["insightId"] == insight_id for item in feed(True)), "includeHidden did not restore hidden insight")
    finally:
        api(f"/insights/{insight_id}/state", method="PUT", body={"hidden": False}, expected=204)
    api(f"/insights/{insight_id}/feedback", method="PUT", body={"rating": "USEFUL", "comment": "SYNTHETIC SMOKE ONLY"}, expected=204)
    action = api(f"/insights/{insight_id}/actions", method="POST", body={"action": "REQUEST_ANALYSIS", "comment": "SYNTHETIC SMOKE ONLY"}, expected=201)[0]
    require(action["status"] == "RECORDED" and action["delivery"] == "LOCAL_ONLY_NO_CRM_DELIVERY", "Action storage/delivery contract mismatch")

    question = api(f"/insights/{insight_id}/questions", method="POST", body={"question": "Какая ставка указана в синтетической новости?"}, key="smoke-question-" + uuid4().hex, expected=202)[0]
    question = eventually(lambda: completed(question["operationId"]), timeout=args.timeout, label="question")
    answer = api("/answers/" + question["operationId"])[0]
    require(answer["insightId"] == insight_id and answer["mlStatus"] == "answered" and answer["claims"], "Question did not return a grounded answer")
    for operation_id in (operation["operationId"], question["operationId"]):
        def trace_ready():
            trace = api("/operations/" + operation_id + "/trace")[0]
            return trace if trace["traceStatus"] == "AVAILABLE" else None
        trace = eventually(trace_ready, timeout=args.timeout, label="persisted trace")
        require(trace["steps"] and trace["profileSnapshot"]["profile"]["company_id"] == "U_1", "Trace/profile snapshot missing")
    final_health = request(args.ml_url, "/smoke/health")[0]
    require(final_health["external_llm_calls"] == 0 and final_health["scripted_calls"] >= 4, "Expected only scripted model calls")
    print(json.dumps({"status": "passed", "mode": "synthetic-scripted", "company_id": "U_1",
                      "analysis_operation": operation["operationId"], "question_operation": question["operationId"],
                      "external_llm_calls": 0, "checks": ["fixture-readiness", "authentication", "csv-import", "profile", "analysis", "idempotency", "feed", "hidden-state", "feedback", "local-action", "question", "traces"]}, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (SmokeFailure, OSError, ValueError, KeyError) as error:
        raise SystemExit(f"Smoke check failed: {error}") from None
