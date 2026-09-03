"""F1 auth + F2 key vault unit/integration tests."""


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["app"] == "AgentAlgo"
    assert "disclaimer" in body


def test_register_login_me(client):
    r = client.post("/api/auth/register", json={"email": "a@b.co", "password": "longenough1"})
    assert r.status_code == 201
    tok = r.json()["access_token"]
    r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200 and r.json()["email"] == "a@b.co"
    # duplicate
    assert client.post("/api/auth/register", json={"email": "a@b.co", "password": "longenough1"}).status_code == 409
    # bad password
    assert client.post("/api/auth/login", json={"email": "a@b.co", "password": "wrongwrong"}).status_code == 401
    # short password rejected
    assert client.post("/api/auth/register", json={"email": "c@d.co", "password": "short"}).status_code == 422


def test_auth_required(client):
    assert client.get("/api/keys").status_code in (401, 403)
    assert client.get("/api/runs").status_code in (401, 403)
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer bogus"}).status_code == 401


def test_key_vault_roundtrip(client, auth):
    r = client.put("/api/keys", headers=auth, json={"provider": "openai", "secret": "sk-verysecretvalue9999"})
    assert r.status_code == 200
    body = r.json()
    # masked, never echoed back
    assert "verysecret" not in str(body)
    assert body["mask"].startswith("sk-") and body["mask"].endswith("9999")
    assert body["status"] == "untested"
    # stored ciphertext is encrypted at rest
    from app.db import SessionLocal
    from app.models import ApiKey
    with SessionLocal() as db:
        row = db.query(ApiKey).filter(ApiKey.provider == "openai").first()
        assert "sk-verysecretvalue9999" not in row.ciphertext
    # unknown provider rejected
    assert client.put("/api/keys", headers=auth, json={"provider": "nope", "secret": "x"}).status_code == 422
    # delete
    assert client.delete("/api/keys/openai", headers=auth).status_code == 204
    assert client.get("/api/keys", headers=auth).json() == []


def test_crypto_helpers():
    from app.security import decrypt_secret, encrypt_secret, hash_password, mask_secret, verify_password

    ct = encrypt_secret("hello")
    assert ct != "hello" and decrypt_secret(ct) == "hello"
    h = hash_password("pw12345678")
    assert verify_password("pw12345678", h) and not verify_password("nope", h)
    assert mask_secret("sk-abcdefgh1234") == "sk-…1234"
    assert mask_secret("tiny") == "…ny"
