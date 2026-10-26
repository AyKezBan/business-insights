#!/usr/bin/env python3
"""Explicit, revision-pinned acceptance harness; dry-run is the default.

Uses the production agent and QA pipeline. All text and provider responses belong
in the caller's private output directory; stdout contains metadata only.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from decimal import Decimal
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time
from urllib.parse import urlsplit

from dotenv import dotenv_values

from insight_agent import schemas
from insight_agent.agent.qa import answer_question
from insight_agent.agent.versions import PROMPT_VERSION
from insight_agent.api import build_agent
from insight_agent.config import Settings
from insight_agent.llm import LLMError, OpenAICompatibleClient, ensure_allowed
from insight_agent.materials.identity import event_content_sha256
from insight_agent.materials.store import atomic_json, load_catalog_token, load_snapshot
from insight_agent.schemas import CompanyProfile, InsightRequest
from insight_agent.trace import load_trace


class RunnerError(ValueError):
    """Messages are fixed metadata, safe for stdout."""


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _dump(value):
    # Preserve future DTO/evidence fields instead of enumerating today's claims.
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if is_dataclass(value):
        # Decimal (usage.cost) не сериализуется в JSON: сохраняем строкой, не теряя точности.
        return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in asdict(value).items()}
    return value


def _redact(value, secret: str):
    if isinstance(value, dict):
        return {key: "[REDACTED]" if key.lower() in {"api_key", "llm_api_key", "authorization", "password"}
                else _redact(item, secret) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item, secret) for item in value]
    if isinstance(value, str) and secret:
        return value.replace(secret, "[REDACTED]")
    return value


def _append(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


class JournalClient:
    """Observe a single production ChatClient, without adding inference/retries.

    Production LLMResponse intentionally only exposes token counts. A response
    hook on that client's existing HTTP transport preserves raw gateway usage
    (including usage.cost) before this information is discarded by the DTO.
    """

    def __init__(self, client, directory: Path, *, secret: str):
        self.client, self.directory, self.secret = client, Path(directory), secret
        self.attempts: list[dict] = []
        self.gateway_usage: list[dict] = []
        self.provider_requests: list[dict] = []
        self._call_id = 0
        if isinstance(client, OpenAICompatibleClient):
            client._http.event_hooks.setdefault("request", []).append(self.observe_request)
            client._http.event_hooks.setdefault("response", []).append(self.observe_response)

    def start_case(self, directory: Path) -> None:
        self.directory = directory
        self.attempts, self.gateway_usage, self.provider_requests = [], [], []

    def observe_request(self, request) -> None:
        # No headers/URL/query are written: credentials stay in the transport.
        body = json.loads(request.content)
        record = _redact({"call_id": self._call_id, "request_number": len(self.provider_requests) + 1,
                          "body": body, "started_at": datetime.now(UTC).isoformat()}, self.secret)
        self.provider_requests.append(record)
        _append(self.directory / "gateway-requests.jsonl", record)

    def observe_response(self, response) -> None:
        response.read()
        try:
            payload = response.json()
        except (ValueError, UnicodeError):
            payload = {"non_json_body": response.text}
        record = _redact({"call_id": self._call_id, "status_code": response.status_code,
                          "payload": payload}, self.secret)
        _append(self.directory / "gateway-responses.jsonl", record)
        usage = payload.get("usage") if isinstance(payload, dict) else None
        if isinstance(usage, dict):
            self.gateway_usage.append(_redact({"call_id": self._call_id, "usage": usage,
                                               "currency": usage.get("currency") or "unknown"}, self.secret))

    def chat(self, model, messages, *, json_mode):
        self._call_id += 1
        started = time.monotonic()
        attempt = {"call_id": self._call_id, "model": model, "json_mode": json_mode,
                   "messages": messages, "started_at": datetime.now(UTC).isoformat()}
        _append(self.directory / "attempts.jsonl", _redact({**attempt, "phase": "started"}, self.secret))
        try:
            response = self.client.chat(model, messages, json_mode=json_mode)
            # A gateway may echo the supplied credential in a generated response.
            # Remove it before the production pipeline can persist derived text.
            if self.secret and self.secret in response.text:
                response.text = response.text.replace(self.secret, "[REDACTED]")
            attempt["response"] = _dump(response)
            return response
        except Exception as exc:
            attempt["error"] = {"type": type(exc).__name__, "message": str(exc)}
            if self.secret and self.secret in str(exc):
                raise LLMError(str(exc).replace(self.secret, "[REDACTED]")) from None
            raise
        finally:
            attempt["latency_ms"] = int((time.monotonic() - started) * 1000)
            safe = _redact(attempt, self.secret)
            self.attempts.append(safe)
            _append(self.directory / "attempts.jsonl", {**safe, "phase": "finished"})


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    for name in ("manifest", "store", "profiles", "out"):
        result.add_argument(f"--{name}", type=Path, required=True)
    result.add_argument("--live", action="store_true", help="Explicitly permit provider calls")
    result.add_argument("--case-id", action="append", help="Run only this frozen case; repeat to select several")
    result.add_argument("--env-file", type=Path, help="Read credentials only in live mode")
    result.add_argument("--model", default="google/gemma-4-26b-a4b-it")
    result.add_argument("--fallback-model", help="Optional allowed model; default uses the same model")
    result.add_argument("--timeout-seconds", type=float, default=60.0)
    result.add_argument("--run-deadline-seconds", type=float, default=120.0)
    result.add_argument("--deadline-seconds", type=float, default=1800.0,
                        help="Stop before starting another case after this session duration")
    result.add_argument("--run-token-budget", type=int, default=40_000)
    result.add_argument("--daily-token-budget", type=int, default=2_000_000)
    result.add_argument("--budget-rub", type=Decimal, default=Decimal("100"),
                        help="Local estimated budget cap; not a gateway invoice")
    result.add_argument("--price-input-rub-per-1k", type=Decimal, default=Decimal("0.5"))
    result.add_argument("--price-output-rub-per-1k", type=Decimal, default=Decimal("1.5"))
    result.add_argument("--max-iterations", type=int, default=3)
    result.add_argument("--materials-public-base-url", default="https://materials.example.test")
    return result


def _validate_options(options) -> None:
    ensure_allowed(options.model)
    ensure_allowed(options.fallback_model or options.model)
    for key in ("timeout_seconds", "run_deadline_seconds", "deadline_seconds", "run_token_budget",
                "daily_token_budget", "budget_rub", "max_iterations"):
        value = getattr(options, key)
        if not math.isfinite(value) or value <= 0:
            raise RunnerError("invalid_positive_limit")
    for key in ("price_input_rub_per_1k", "price_output_rub_per_1k"):
        value = getattr(options, key)
        if not value.is_finite() or value < 0:
            raise RunnerError("invalid_price_estimate")


def _preflight(options):
    manifest_bytes = options.manifest.read_bytes()
    manifest = json.loads(manifest_bytes)
    if not manifest.get("frozen_at"):
        raise RunnerError("manifest_not_frozen")
    profiles_bytes = options.profiles.read_bytes()
    if _hash(profiles_bytes) != manifest["profiles_file_sha256"]:
        raise RunnerError("profiles_file_hash_mismatch")
    raw_profiles = json.loads(profiles_bytes)["profiles"]
    profiles = {item["company_id"]: CompanyProfile.model_validate(item) for item in raw_profiles}
    if len(profiles) != len(raw_profiles):
        raise RunnerError("duplicate_company_id")
    snapshot = load_snapshot(options.store, manifest["corpus_revision"])
    token = load_catalog_token(options.store, manifest["corpus_revision"])
    if token.model_dump(mode="json") != manifest["catalog_token"]:
        raise RunnerError("catalog_token_mismatch")
    events = {event.event_id: event for event in snapshot.news}
    selected_events = {}
    for expected in manifest["events"]:
        event = events.get(expected["event_id"])
        if event is None or event_content_sha256(event) != expected["event_content_sha256"]:
            raise RunnerError("event_hash_mismatch")
        if event.source.url != expected["url"]:
            raise RunnerError("event_source_url_mismatch")
        for name in ("source_id", "content_sha256"):
            if name in expected and getattr(event.source, name) != expected[name]:
                raise RunnerError("event_source_identity_mismatch")
        if event.event_id in selected_events:
            raise RunnerError("duplicate_event_id")
        selected_events[event.event_id] = event
    cases, pair_ids = [], set()
    for pair in manifest["pairs"]:
        case_id = pair["case_id"]
        if not isinstance(case_id, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", case_id) is None:
            raise RunnerError("invalid_case_id")
        identity = (pair["event_id"], pair["company_id"])
        if case_id in {case["case_id"] for case in cases} or identity in pair_ids:
            raise RunnerError("duplicate_case_or_pair")
        pair_ids.add(identity)
        if pair["event_id"] not in selected_events or pair["company_id"] not in profiles:
            raise RunnerError("unknown_event_or_company")
        if not isinstance(pair["question"], str) or not pair["question"].strip():
            raise RunnerError("missing_qa_question")
        request = InsightRequest(profile=profiles[pair["company_id"]], role=pair["role"],
                                 event=selected_events[pair["event_id"]],
                                 corpus_revision=snapshot.revision,
                                 event_content_sha256=event_content_sha256(selected_events[pair["event_id"]]))
        # Includes the question and file hash: changed QA/profile inputs cannot reuse a result.
        fingerprint = _hash(_canonical({"request": _dump(request), "question": pair["question"],
                                       "manifest_sha256": _hash(manifest_bytes),
                                       "profiles_file_sha256": _hash(profiles_bytes)}))
        request.request_id = f"release-{fingerprint[:48]}"
        cases.append({**pair, "request": request, "fingerprint": fingerprint,
                      "profile_sha256": _hash(_canonical(_dump(request.profile)))})
    if not cases:
        raise RunnerError("no_cases")
    wanted = set(options.case_id or [case["case_id"] for case in cases])
    if wanted - {case["case_id"] for case in cases}:
        raise RunnerError("unknown_selected_case")
    return manifest, [case for case in cases if case["case_id"] in wanted]


def _pending(options, cases):
    pending, skipped = [], 0
    for case in cases:
        directory = options.out / "cases" / case["case_id"]
        if not directory.exists():
            pending.append(case)
            continue
        if directory.is_symlink() or not (directory / "result.json").is_file():
            raise RunnerError("incomplete_case_requires_manual_review")
        previous = json.loads((directory / "result.json").read_bytes())
        if previous.get("journal_status") != "completed":
            raise RunnerError("incomplete_case_requires_manual_review")
        if previous.get("identity", {}).get("fingerprint") != case["fingerprint"]:
            raise RunnerError("completed_case_input_mismatch")
        skipped += 1
    return pending, skipped


def _validate_output(path: Path) -> None:
    if path.is_symlink():
        raise RunnerError("output_must_be_private_directory")
    if not path.exists():
        return
    if not path.is_dir() or path.stat().st_mode & 0o077:
        raise RunnerError("output_must_be_private_directory")
    # Existing nested links can redirect traces/journals outside the caller's
    # private directory, including links that do not resolve yet.
    if any(child.is_symlink() for child in path.rglob("*")):
        raise RunnerError("output_contains_symlink")


def _credentials(options):
    # This function is deliberately unreachable from dry-run and completed-only resume.
    values = dotenv_values(options.env_file) if options.env_file else {}
    key = os.environ.get("LLM_API_KEY") or os.environ.get("API_KEY") or values.get("LLM_API_KEY") or values.get("API_KEY")
    url = os.environ.get("LLM_BASE_URL") or os.environ.get("BASE_URL") or values.get("LLM_BASE_URL") or values.get("BASE_URL")
    if not key or not url:
        raise RunnerError("live_credentials_missing")
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RunnerError("invalid_gateway_base_url")
    return key, url


def _versions():
    root = Path(__file__).resolve().parents[2]
    code = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    return {"code": code, "prompt": PROMPT_VERSION,
            "schema_sha256": _hash(Path(schemas.__file__).read_bytes()),
            "runner_sha256": _hash(Path(__file__).read_bytes())}


def _execute_case(agent, journal, case, manifest, versions, options):
    directory = options.out / "cases" / case["case_id"]
    directory.mkdir(parents=True, exist_ok=False, mode=0o700)
    identity = {"fingerprint": case["fingerprint"], "profile_sha256": case["profile_sha256"],
                "profiles_file_sha256": manifest["profiles_file_sha256"],
                "corpus_revision": manifest["corpus_revision"], "catalog_token": manifest["catalog_token"],
                "event_content_sha256": case["request"].event_content_sha256}
    result = {"case_id": case["case_id"], "identity": identity, "versions": versions,
              "manual_verdict": "NOT_REVIEWED", "journal_status": "started",
              "request": _dump(case["request"]), "qa_question": case["question"],
              "started_at": datetime.now(UTC).isoformat(), "insight": None, "qa": None, "errors": []}
    atomic_json(directory / "started.json", _redact(result, journal.secret))
    journal.start_case(directory)
    started = time.monotonic()
    trace_ids = {}
    try:
        insight = agent.run(case["request"])
        result["insight"] = _dump(insight)
        trace_ids["insight"] = insight.run_id
        answer = answer_question(agent, insight.insight_id, case["question"])
        result["qa"] = _dump(answer)
        if answer is not None:
            trace_ids["qa"] = answer.run_id
        if insight.status == "failed" or answer is None:
            result["errors"].append({"type": "pipeline_incomplete", "code": insight.error_code})
    except Exception as exc:
        result["errors"].append({"type": type(exc).__name__, "message": str(exc)})
    result["latency_ms"] = int((time.monotonic() - started) * 1000)
    result["traces"] = {stage: [_dump(event) for event in (load_trace(agent.settings.trace_dir, run_id) or [])]
                        for stage, run_id in trace_ids.items()}
    for event in result["traces"].get("qa", []):
        # Production QA deliberately returns no_answer for both absent evidence
        # and caught LLM/budget/corpus errors; only the latter are run failures.
        if event["type"] == "error" and event["step"] in {"answer", "question_corpus"}:
            result["errors"].append({"type": "qa_execution_failed", "step": event["step"], "run_id": event["run_id"]})
    result["attempts"] = journal.attempts
    result["provider_requests"] = len(journal.provider_requests) if isinstance(journal.client, OpenAICompatibleClient) else len(journal.attempts)
    local_estimated = sum(Decimal(event["data"]["cost_rub"] or "0")
                          for events in result["traces"].values() for event in events
                          if event["type"] == "llm_call")
    result["accounting"] = {
        "gateway_usage": journal.gateway_usage,
        "local_estimate": {"currency": "RUB", "is_estimate": True, "cost": str(local_estimated),
                           "input_rub_per_1k": str(options.price_input_rub_per_1k),
                           "output_rub_per_1k": str(options.price_output_rub_per_1k)},
        "counter_delta_is_own_cost": False,
    }
    result["journal_status"] = "completed"
    result["finished_at"] = datetime.now(UTC).isoformat()
    atomic_json(directory / "result.json", _redact(result, journal.secret))
    return result


def main(argv=None, *, client_factory=None) -> int:
    options = parser().parse_args(argv)
    summary = {"mode": "live" if options.live else "dry-run", "provider_calls": 0,
               "completed_cases": 0, "skipped_cases": 0}
    client = None
    previous_umask = None
    try:
        _validate_options(options)
        manifest, cases = _preflight(options)
        _validate_output(options.out)
        pending, skipped = _pending(options, cases)
        summary.update(selected_cases=len(cases), skipped_cases=skipped,
                       corpus_revision=manifest["corpus_revision"], manual_verdict="NOT_REVIEWED")
        if not options.live or not pending:
            print(json.dumps(summary))
            return 0
        key, url = _credentials(options)
        versions = _versions()
        previous_umask = os.umask(0o077)
        options.out.mkdir(parents=True, exist_ok=True, mode=0o700)
        _validate_output(options.out)
        settings = Settings(_env_file=None, llm_api_key=key, llm_base_url=url,
                            llm_model_light=options.model, llm_model_reasoning=options.model,
                            llm_model_fallback=options.fallback_model or options.model,
                            llm_timeout_seconds=options.timeout_seconds,
                            agent_max_seconds=options.run_deadline_seconds,
                            agent_max_iterations=options.max_iterations,
                            agent_max_tokens_per_run=options.run_token_budget,
                            llm_daily_token_budget=options.daily_token_budget,
                            llm_daily_budget_rub=options.budget_rub,
                            llm_budget_warn_rub=options.budget_rub * Decimal("0.7"),
                            llm_budget_stop_optional_rub=options.budget_rub * Decimal("0.9"),
                            llm_price_default_in_rub_per_1k=options.price_input_rub_per_1k,
                            llm_price_default_out_rub_per_1k=options.price_output_rub_per_1k,
                            llm_prices_json="", budget_state_path=options.out / "budget-state.json",
                            materials_dir=options.store, materials_public_base_url=options.materials_public_base_url,
                            trace_dir=options.out / "traces", open_research_enabled=False)
        client = client_factory(settings) if client_factory else OpenAICompatibleClient(url, key, options.timeout_seconds)
        journal = JournalClient(client, options.out, secret=key)
        agent = build_agent(settings, journal)
        deadline = time.monotonic() + options.deadline_seconds
        for case in pending:
            if time.monotonic() >= deadline:
                raise RunnerError("session_deadline_exceeded")
            result = _execute_case(agent, journal, case, manifest, versions, options)
            summary["completed_cases"] += 1
            summary["provider_calls"] += result["provider_requests"]
            if result["errors"]:
                summary["error_code"] = "case_execution_failed"
                print(json.dumps(summary))
                return 1
        print(json.dumps(summary))
        return 0
    except Exception as exc:
        summary["error_code"] = str(exc) if isinstance(exc, RunnerError) else type(exc).__name__
        print(json.dumps(summary))
        return 2
    finally:
        if isinstance(client, OpenAICompatibleClient):
            client._http.close()
        if previous_umask is not None:
            os.umask(previous_umask)


if __name__ == "__main__":
    raise SystemExit(main())
