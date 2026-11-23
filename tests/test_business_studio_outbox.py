import threading
import time
from concurrent.futures import ThreadPoolExecutor

from business_app.provisioning import business_studio


class FakeCursor:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class FakeOutboxConnection:
    def __init__(self, state, lock):
        self.state = state
        self.lock = lock

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, sql, params=()):
        normalized = " ".join(sql.split())
        if "last_error = 'Webhook delivery lease expired" in normalized:
            proposal_id, max_attempts, lease_seconds = params
            with self.lock:
                if (
                    self.state["proposal_id"] == proposal_id
                    and self.state["status"] == "SENDING"
                    and self.state["attempts"] >= max_attempts
                    and time.monotonic() - self.state["updated_at"] > lease_seconds
                ):
                    self.state.update(status="FAILED", last_error="lease expired")
            return FakeCursor()

        if "RETURNING event_id, payload, attempts" in normalized:
            proposal_id, max_attempts, lease_seconds = params
            with self.lock:
                pending_due = (
                    self.state["status"] in {"PENDING", "RETRY"}
                    and self.state["next_attempt_at"] <= time.monotonic()
                )
                abandoned_lease = (
                    self.state["status"] == "SENDING"
                    and time.monotonic() - self.state["updated_at"] > lease_seconds
                )
                if (
                    self.state["proposal_id"] != proposal_id
                    or self.state["attempts"] >= max_attempts
                    or not (pending_due or abandoned_lease)
                ):
                    return FakeCursor()
                self.state.update(
                    status="SENDING",
                    attempts=self.state["attempts"] + 1,
                    updated_at=time.monotonic(),
                )
                return FakeCursor((
                    self.state["event_id"],
                    self.state["payload"],
                    self.state["attempts"],
                ))

        if "UPDATE business_automation_outbox" in normalized:
            with self.lock:
                if "next_attempt_at = NOW()" in normalized:
                    status, error, delay, event_id = params
                    self.state.update(
                        status=status,
                        last_error=error,
                        next_attempt_delay=delay,
                        next_attempt_at=time.monotonic() + delay,
                        updated_at=time.monotonic(),
                    )
                else:
                    status, error, delivered_status, event_id = params
                    self.state.update(
                        status=status,
                        last_error=error,
                        next_attempt_at=None,
                        delivered_at=(time.monotonic() if delivered_status == "SENT" else None),
                        updated_at=time.monotonic(),
                    )
            return FakeCursor()

        if "UPDATE business_proposals" in normalized:
            status, proposal_id = params
            with self.lock:
                self.state["proposal_webhook_status"] = status
            return FakeCursor()

        raise AssertionError(f"Unexpected SQL in outbox test: {normalized}")


class FakeBusinessStudioStore(business_studio.BusinessStudioStore):
    def __init__(self, state):
        super().__init__(database_url="postgresql://unit-test.invalid/test")
        self.state = state
        self.lock = threading.Lock()

    @staticmethod
    def _ensure_schema(conn):
        return None

    def _connect(self):
        return FakeOutboxConnection(self.state, self.lock)


class FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


def make_state(**overrides):
    state = {
        "proposal_id": "proposal-123",
        "event_id": "event-123",
        "payload": {"event": "business.approved", "proposalId": "proposal-123"},
        "status": "PENDING",
        "attempts": 0,
        "last_error": None,
        "next_attempt_at": 0,
        "updated_at": time.monotonic(),
        "next_attempt_delay": None,
        "delivered_at": None,
        "proposal_webhook_status": "QUEUED",
    }
    state.update(overrides)
    return state


def test_outbox_claim_prevents_two_workers_from_sending_the_same_event(monkeypatch):
    state = make_state()
    store = FakeBusinessStudioStore(state)
    send_started = threading.Event()
    release_send = threading.Event()
    sent_keys = []

    def slow_urlopen(request, timeout):
        sent_keys.append(request.get_header("Idempotency-key"))
        send_started.set()
        assert release_send.wait(3)
        return FakeResponse()

    monkeypatch.setattr(business_studio, "urlopen", slow_urlopen)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(
            store.dispatch_make_event, "proposal-123",
            "https://hooks.example.test/approved", "test-secret",
        )
        assert send_started.wait(2)
        second = store.dispatch_make_event(
            "proposal-123", "https://hooks.example.test/approved", "test-secret"
        )
        release_send.set()
        first_result = first.result(timeout=5)

    assert first_result["status"] == "SENT"
    assert second["status"] == "NOT_DUE_OR_ALREADY_CLAIMED"
    assert sent_keys == ["event-123"]
    assert state["attempts"] == 1
    assert state["status"] == "SENT"
    assert state["proposal_webhook_status"] == "SENT"
    assert state["delivered_at"] is not None


def test_outbox_failure_uses_exponential_backoff_and_does_not_retry_early(monkeypatch):
    state = make_state()
    store = FakeBusinessStudioStore(state)
    calls = []

    def failed_urlopen(request, timeout):
        calls.append(request)
        raise TimeoutError("sensitive endpoint details should not be stored")

    monkeypatch.setattr(business_studio, "urlopen", failed_urlopen)
    result = store.dispatch_make_event(
        "proposal-123", "https://hooks.example.test/approved", "test-secret"
    )
    second = store.dispatch_make_event(
        "proposal-123", "https://hooks.example.test/approved", "test-secret"
    )

    assert result["status"] == "RETRY"
    assert result["attempts"] == 1
    assert result["nextAttemptInSeconds"] == 30
    assert state["last_error"] == "Webhook delivery failed; sensitive provider response was not stored."
    assert second["status"] == "NOT_DUE_OR_ALREADY_CLAIMED"
    assert len(calls) == 1


def test_outbox_stops_after_maximum_attempts(monkeypatch):
    state = make_state(status="RETRY", attempts=4, next_attempt_at=0)
    store = FakeBusinessStudioStore(state)

    def failed_urlopen(request, timeout):
        raise TimeoutError("temporary delivery failure")

    monkeypatch.setattr(business_studio, "urlopen", failed_urlopen)
    result = store.dispatch_make_event(
        "proposal-123", "https://hooks.example.test/approved", "test-secret"
    )

    assert result["status"] == "FAILED"
    assert result["attempts"] == 5
    assert state["status"] == "FAILED"
    assert state["next_attempt_at"] is None
