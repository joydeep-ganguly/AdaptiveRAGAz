# backend/tests/unit/test_scaling_idempotency.py

from app.scaling.idempotency import IDEMPOTENCY_KEY_PREFIX, request_fingerprint


def test_first_claim_wins_and_can_be_looked_up(idempotency_store):
    assert idempotency_store.claim(1, "key-1", "job-a", "fp") is None

    entry = idempotency_store.lookup(1, "key-1")
    assert entry.job_id == "job-a"
    assert entry.fingerprint == "fp"


def test_second_claim_gets_the_first_entry(idempotency_store):
    idempotency_store.claim(1, "key-1", "job-a", "fp")

    existing = idempotency_store.claim(1, "key-1", "job-b", "fp")

    assert existing.job_id == "job-a"  # the first job is kept


def test_keys_are_separate_per_user(idempotency_store):
    idempotency_store.claim(1, "key-1", "job-a", "fp")

    assert idempotency_store.claim(2, "key-1", "job-b", "fp") is None  # user 2 wins their own claim
    assert idempotency_store.lookup(2, "key-1").job_id == "job-b"


def test_release_frees_the_key(idempotency_store):
    idempotency_store.claim(1, "key-1", "job-a", "fp")

    idempotency_store.release(1, "key-1")

    assert idempotency_store.lookup(1, "key-1") is None
    assert idempotency_store.claim(1, "key-1", "job-b", "fp") is None


def test_key_has_an_expiry(idempotency_store, fake_redis):
    idempotency_store.claim(1, "key-1", "job-a", "fp")

    assert 0 < fake_redis.ttl(f"{IDEMPOTENCY_KEY_PREFIX}1:key-1") <= 3600


def test_fingerprint_depends_on_conversation_and_message():
    base = request_fingerprint("conv-1", "hello")

    assert request_fingerprint("conv-1", "hello") == base
    assert request_fingerprint("conv-1", "hello!") != base
    assert request_fingerprint("conv-2", "hello") != base