from datetime import date
import json
import base64

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

import pytest
from sqlalchemy import select

from app.api.v1 import auth as auth_api
from app.core.security import create_access_token
from app.models import HealthCheckIn, User, UserIdentity, UserIdentityLinkCode
from app.services.privacy import delete_account_data


class FakeWechatResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeWechatClient:
    payload = {"openid": "mobile-subject-1", "unionid": "union-subject-1"}
    requests = []

    def __init__(self, **kwargs):
        self.options = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, url, *, params):
        self.requests.append((url, dict(params), self.options))
        return FakeWechatResponse(self.payload)


@pytest.fixture(autouse=True)
def mobile_auth_config(monkeypatch):
    FakeWechatClient.requests = []
    FakeWechatClient.payload = {"openid": "mobile-subject-1", "unionid": "union-subject-1"}
    monkeypatch.setattr(auth_api.settings, "mobile_wechat_app_id", "mobile-app-test")
    monkeypatch.setattr(auth_api.settings, "mobile_wechat_app_secret", "server-only-secret")
    monkeypatch.setattr(auth_api.httpx, "AsyncClient", FakeWechatClient)


def test_mobile_wechat_login_uses_mobile_credentials_and_scoped_identity(api, db):
    first = api.post("/api/v1/auth/mobile/wechat", json={"code": "temporary-code"})
    second = api.post("/api/v1/auth/mobile/wechat", json={"code": "another-temporary-code"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["user"]["id"] == second.json()["user"]["id"]
    assert len(FakeWechatClient.requests) == 2
    url, params, options = FakeWechatClient.requests[0]
    assert url == "https://api.weixin.qq.com/sns/oauth2/access_token"
    assert params["appid"] == "mobile-app-test"
    assert params["secret"] == "server-only-secret"
    assert options["follow_redirects"] is False

    identity = db.scalar(
        select(UserIdentity).where(
            UserIdentity.provider == "wechat_mobile",
            UserIdentity.issuer == "mobile-app-test",
            UserIdentity.subject == "mobile-subject-1",
        )
    )
    user = db.get(User, identity.user_id)
    assert user.openid is None
    assert identity.union_subject == "union-subject-1"
    assert "server-only-secret" not in first.text


def test_mobile_login_fails_closed_when_open_platform_credentials_are_missing(api, monkeypatch):
    monkeypatch.setattr(auth_api.settings, "mobile_wechat_app_id", "")
    monkeypatch.setattr(auth_api.settings, "mobile_wechat_app_secret", "")

    response = api.post("/api/v1/auth/mobile/wechat", json={"code": "temporary-code"})

    assert response.status_code == 503
    assert FakeWechatClient.requests == []


def test_mobile_auth_routes_are_disabled_until_profile_opts_in(api, monkeypatch):
    monkeypatch.setattr(auth_api.settings, "mobile_auth_enabled", False)

    responses = [
        api.post("/api/v1/auth/mobile/wechat", json={"code": "temporary-code"}),
        api.post("/api/v1/auth/cloudbase/ticket"),
        api.post("/api/v1/auth/link/start"),
        api.post("/api/v1/auth/link/complete", json={"link_code": "A" * 32}),
        api.post("/api/v1/auth/link/unlink"),
    ]

    assert [response.status_code for response in responses] == [503] * 5
    assert FakeWechatClient.requests == []


def test_mobile_login_handles_malformed_wechat_error_code(api, monkeypatch):
    FakeWechatClient.payload = {"errcode": {"unexpected": "shape"}}

    response = api.post("/api/v1/auth/mobile/wechat", json={"code": "temporary-code"})

    assert response.status_code == 503
    assert "unexpected" not in response.text


def test_mini_program_login_upgrades_backfilled_legacy_identity(api, db, monkeypatch):
    monkeypatch.setattr(auth_api.settings, "wechat_app_id", "mini-app-test")
    existing = User(openid="existing-mini-openid", nickname="旧昵称")
    db.add(existing)
    db.flush()
    user_id = existing.id
    db.add(
        UserIdentity(
            user_id=user_id,
            provider="wechat_miniprogram",
            issuer="legacy",
            subject="existing-mini-openid",
        )
    )
    db.commit()

    user = auth_api.upsert(db, "existing-mini-openid", "新昵称")

    assert user.id == user_id
    identity = db.scalar(
        select(UserIdentity).where(
            UserIdentity.user_id == user_id,
            UserIdentity.provider == "wechat_miniprogram",
        )
    )
    assert identity.issuer == "mini-app-test"
    assert identity.subject == "existing-mini-openid"


def test_link_complete_rejects_non_hex_association_code(api):
    response = api.post(
        "/api/v1/auth/link/complete",
        json={"link_code": "A" * 31 + "!"},
    )

    assert response.status_code == 422


def test_identity_link_moves_only_an_empty_mobile_account(api, db):
    mini_user = db.get(User, api.user_id)
    db.add(
        UserIdentity(
            user_id=mini_user.id,
            provider="wechat_miniprogram",
            issuer="mini-app-test",
            subject="mini-subject-1",
        )
    )
    mobile_user = User(openid=None, nickname="Android")
    db.add(mobile_user)
    db.flush()
    mobile_user_id = mobile_user.id
    db.add(
        UserIdentity(
            user_id=mobile_user_id,
            provider="wechat_mobile",
            issuer="mobile-app-test",
            subject="mobile-subject-link",
        )
    )
    db.commit()

    started = api.post("/api/v1/auth/link/start")
    assert started.status_code == 200
    code = started.json()["link_code"]

    api.headers["Authorization"] = "Bearer " + create_access_token(str(mobile_user_id))
    completed = api.post("/api/v1/auth/link/complete", json={"link_code": code.lower()})
    assert completed.status_code == 200
    assert completed.json()["user"]["id"] == mini_user.id
    assert db.get(User, mobile_user_id) is None
    moved = db.scalar(
        select(UserIdentity).where(
            UserIdentity.provider == "wechat_mobile",
            UserIdentity.subject == "mobile-subject-link",
        )
    )
    assert moved.user_id == mini_user.id
    link = db.scalar(select(UserIdentityLinkCode).where(UserIdentityLinkCode.source_user_id == mini_user.id))
    assert link.consumed_at is not None

    api.headers["Authorization"] = "Bearer " + completed.json()["access_token"]
    repeated = api.post("/api/v1/auth/link/complete", json={"link_code": code})
    assert repeated.status_code == 409


def test_identity_link_refuses_to_merge_mobile_health_data(api, db):
    mini_user = db.get(User, api.user_id)
    db.add(
        UserIdentity(
            user_id=mini_user.id,
            provider="wechat_miniprogram",
            issuer="mini-app-test",
            subject="mini-subject-data-test",
        )
    )
    mobile_user = User(openid=None, nickname="Android")
    db.add(mobile_user)
    db.flush()
    mobile_user_id = mobile_user.id
    db.add(
        UserIdentity(
            user_id=mobile_user_id,
            provider="wechat_mobile",
            issuer="mobile-app-test",
            subject="mobile-subject-has-data",
        )
    )
    db.add(HealthCheckIn(user_id=mobile_user_id, record_date=date.today().isoformat()))
    db.commit()

    started = api.post("/api/v1/auth/link/start")
    api.headers["Authorization"] = "Bearer " + create_access_token(str(mobile_user_id))
    response = api.post(
        "/api/v1/auth/link/complete",
        json={"link_code": started.json()["link_code"]},
    )

    assert response.status_code == 409
    assert db.get(User, mobile_user_id) is not None


def test_mobile_identity_cannot_be_unlinked_as_the_only_login(api, db):
    user = db.get(User, api.user_id)
    db.add(
        UserIdentity(
            user_id=user.id,
            provider="wechat_mobile",
            issuer="mobile-app-test",
            subject="only-mobile-identity",
        )
    )
    db.commit()

    response = api.post("/api/v1/auth/link/unlink")

    assert response.status_code == 409
    assert db.scalar(
        select(UserIdentity).where(
            UserIdentity.user_id == user.id,
            UserIdentity.provider == "wechat_mobile",
        )
    ) is not None


def test_mobile_identity_can_be_unlinked_when_mini_program_login_remains(api, db):
    user = db.get(User, api.user_id)
    db.add_all(
        [
            UserIdentity(
                user_id=user.id,
                provider="wechat_miniprogram",
                issuer="mini-app-test",
                subject="linked-mini-identity",
            ),
            UserIdentity(
                user_id=user.id,
                provider="wechat_mobile",
                issuer="mobile-app-test",
                subject="linked-mobile-identity",
            ),
        ]
    )
    db.commit()

    response = api.post("/api/v1/auth/link/unlink")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "unlinked": True}
    assert db.scalar(
        select(UserIdentity).where(
            UserIdentity.user_id == user.id,
            UserIdentity.provider == "wechat_mobile",
        )
    ) is None
    assert db.scalar(
        select(UserIdentity).where(
            UserIdentity.user_id == user.id,
            UserIdentity.provider == "wechat_miniprogram",
        )
    ) is not None


def test_privacy_deletion_removes_mobile_identities_and_pending_link_codes(api, db):
    user = db.get(User, api.user_id)
    db.add(
        UserIdentity(
            user_id=user.id,
            provider="wechat_mobile",
            issuer="mobile-app-test",
            subject="identity-to-delete",
        )
    )
    db.add(
        UserIdentityLinkCode(
            code_hash="a" * 64,
            source_user_id=user.id,
            expires_at=auth_api.utc_now(),
        )
    )
    db.commit()

    delete_account_data(db, user.id)

    assert db.get(User, user.id) is None
    assert db.scalar(select(UserIdentity).where(UserIdentity.subject == "identity-to-delete")) is None
    assert db.scalar(select(UserIdentityLinkCode).where(UserIdentityLinkCode.code_hash == "a" * 64)) is None


def test_cloudbase_ticket_is_user_scoped_short_lived_and_verifiable(api, monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")
    monkeypatch.setattr(auth_api.settings, "cloudbase_env_id", "healthmate-test-env")
    monkeypatch.setattr(
        auth_api.settings,
        "cloudbase_custom_login_credentials_json",
        json.dumps({
            "env_id": "healthmate-test-env",
            "private_key_id": "test-key-1",
            "private_key": private_pem,
        }),
    )

    response = api.post("/api/v1/auth/cloudbase/ticket")

    assert response.status_code == 200
    result = response.json()
    assert result["uid"] == f"hm_user_{api.user_id}"
    assert result["expires_in"] == 600
    key_id, marker, jwt = result["ticket"].partition("/@@/")
    assert (key_id, marker) == ("test-key-1", "/@@/")
    encoded_header, encoded_payload, encoded_signature = jwt.split(".")
    decode = lambda part: base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))
    payload = json.loads(decode(encoded_payload))
    assert payload["uid"] == result["uid"]
    assert payload["env"] == "healthmate-test-env"
    assert payload["exp"] - payload["iat"] == 600_000
    key.public_key().verify(
        decode(encoded_signature),
        f"{encoded_header}.{encoded_payload}".encode("ascii"),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )


def test_cloudbase_ticket_fails_closed_for_mismatched_environment(api, monkeypatch):
    monkeypatch.setattr(auth_api.settings, "cloudbase_env_id", "healthmate-test-env")
    monkeypatch.setattr(
        auth_api.settings,
        "cloudbase_custom_login_credentials_json",
        json.dumps({"env_id": "some-other-env", "private_key_id": "key", "private_key": "bad"}),
    )

    response = api.post("/api/v1/auth/cloudbase/ticket")

    assert response.status_code == 503
    assert "some-other-env" not in response.text
