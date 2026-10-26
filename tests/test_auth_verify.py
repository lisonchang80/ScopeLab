"""verify_google_credential：驗簽（委派 google-auth，傳 client id 等於驗 aud + 驗 exp）之外，
額外把關 iss 與 email_verified——未驗證的 email 不得登入成站主。"""
import pytest

from src import auth


@pytest.fixture()
def fake_google(monkeypatch):
    """stub google-auth：不連網、不驗真簽章，只回我們指定的 claims。
    直接打補丁在真模組的屬性上（verify_google_credential 用 `from ... import` 取值，
    patch sys.modules 會被已載入的真模組蓋過，故改 patch 屬性）。"""
    import google.auth.transport.requests as g_requests
    import google.oauth2.id_token as gid

    captured = {}
    claims = {"value": {"iss": "https://accounts.google.com", "email": "owner@example.com",
                        "email_verified": True, "aud": auth.GOOGLE_CLIENT_ID}}

    def verify(credential, request, audience, clock_skew_in_seconds=0):
        captured["audience"] = audience
        return claims["value"]

    monkeypatch.setattr(g_requests, "Request", lambda *a, **k: object())
    monkeypatch.setattr(gid, "verify_oauth2_token", verify)
    return claims, captured


def test_passes_client_id_as_audience(fake_google):
    _, captured = fake_google
    auth.verify_google_credential("x.y.z")
    assert captured["audience"] == auth.GOOGLE_CLIENT_ID


def test_rejects_unverified_email(fake_google):
    claims, _ = fake_google
    claims["value"] = dict(claims["value"], email_verified=False)
    with pytest.raises(Exception):
        auth.verify_google_credential("x.y.z")


def test_rejects_bad_issuer(fake_google):
    claims, _ = fake_google
    claims["value"] = dict(claims["value"], iss="evil.example.com")
    with pytest.raises(Exception):
        auth.verify_google_credential("x.y.z")


def test_accepts_verified_owner(fake_google):
    info = auth.verify_google_credential("x.y.z")
    assert info["email"] == "owner@example.com"
