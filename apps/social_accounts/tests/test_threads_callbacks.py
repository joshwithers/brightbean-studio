"""Threads callback authentication, app isolation and deletion safety."""

import base64
import hashlib
import hmac
import json
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.composer.models import PlatformPost, Post
from apps.credentials.models import PlatformCredential
from apps.organizations.models import Organization
from apps.social_accounts.models import SocialAccount, ThreadsCallbackReceipt
from apps.workspaces.models import Workspace

pytestmark = pytest.mark.django_db
TEST_SECRET = "threads-callback-test-value"


def signed(secret=TEST_SECRET, **overrides):
    data = {
        "algorithm": "HMAC-SHA256",
        "user_id": "123456",
        "issued_at": int(timezone.now().timestamp()),
    }
    data.update(overrides)
    payload = base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")
    signature = base64.urlsafe_b64encode(hmac.digest(secret.encode(), payload.encode(), "sha256"))
    return f"{signature.decode().rstrip('=')}.{payload}"


@pytest.fixture(autouse=True)
def env_credentials(settings):
    settings.APP_URL = "https://studio.example.com"
    settings.PLATFORM_CREDENTIALS_FROM_ENV = {"threads": {"app_id": "app-a", "app_secret": TEST_SECRET}}


@pytest.fixture
def account(organization):
    workspace = Workspace.objects.create(name="Workspace", organization=organization)
    result = SocialAccount.objects.create(
        workspace=workspace,
        platform="threads",
        account_platform_id="123456",
        account_name="Threads test account",
        oauth_access_token="test-token",
    )
    SocialAccount.objects.filter(pk=result.pk).update(connected_at=timezone.now() - timedelta(days=1))
    return result


def post_callback(action="threads_delete", value=None):
    return Client(enforce_csrf_checks=True).post(
        reverse(f"social_accounts:{action}"), {"signed_request": signed() if value is None else value}
    )


@pytest.mark.parametrize("action", ["threads_delete", "threads_deauthorize"])
def test_removes_only_matching_threads_data(account, action):
    other_platform = SocialAccount.objects.create(
        workspace=account.workspace, platform="facebook", account_platform_id="123456", account_name="Facebook"
    )
    other_user = SocialAccount.objects.create(
        workspace=account.workspace, platform="threads", account_platform_id="999", account_name="Other Threads"
    )
    orphan = Post.objects.create(workspace=account.workspace, caption="Threads only")
    PlatformPost.objects.create(post=orphan, social_account=account)
    shared = Post.objects.create(workspace=account.workspace, caption="Shared post")
    PlatformPost.objects.create(post=shared, social_account=account)
    survivor = PlatformPost.objects.create(post=shared, social_account=other_platform)

    response = post_callback(action)
    assert response.status_code == 200
    assert not SocialAccount.objects.filter(pk=account.pk).exists()
    assert SocialAccount.objects.filter(pk__in=[other_platform.pk, other_user.pk]).count() == 2
    assert not Post.objects.filter(pk=orphan.pk).exists()
    assert Post.objects.filter(pk=shared.pk).exists()
    assert PlatformPost.objects.filter(pk=survivor.pk).exists()


@pytest.mark.parametrize(
    "value",
    [
        "",
        "not-a-signed-request",
        "x.y",
        "a" * 8193,
        signed(secret="wrong-secret"),
        signed(algorithm="none"),
        signed(user_id=""),
        signed(user_id="123 OR 1=1"),
        signed(user_id=123456),
        signed(issued_at=None),
        signed(issued_at=True),
        signed(issued_at=99999999999),
        signed(expires=1),
        signed(expires="never"),
    ],
)
def test_invalid_requests_never_delete(account, value):
    assert post_callback(value=value).status_code == 400
    assert SocialAccount.objects.filter(pk=account.pk).exists()
    assert ThreadsCallbackReceipt.objects.count() == 0


def test_payload_tampering_rejected(account):
    original = signed()
    replacement = signed(user_id="999").split(".")[1]
    tampered = original.split(".")[0] + "." + replacement
    assert post_callback(value=tampered).status_code == 400
    assert SocialAccount.objects.filter(pk=account.pk).exists()


def test_receipt_is_private_and_has_valid_status_url(account):
    response = post_callback()
    data = response.json()
    receipt = ThreadsCallbackReceipt.objects.get()
    assert data["confirmation_code"] == receipt.id.hex
    assert data["url"].startswith("https://studio.example.com/social-accounts/threads/deletion-status/")
    status = Client().get(data["url"])
    assert status.status_code == 200
    assert b"Threads data deletion complete" in status.content
    assert b"Threads test account" not in status.content
    assert b"test-token" not in status.content
    assert status["Cache-Control"] == "no-store"
    assert status["X-Robots-Tag"] == "noindex, nofollow"
    assert (
        Client()
        .get(
            reverse(
                "social_accounts:threads_deletion_status", kwargs={"receipt_id": "00000000-0000-0000-0000-000000000000"}
            )
        )
        .status_code
        == 404
    )


def test_retry_cannot_delete_a_later_reconnection(account):
    value = signed()
    first = post_callback(value=value)
    replacement = SocialAccount.objects.create(
        workspace=account.workspace, platform="threads", account_platform_id="123456", account_name="Reconnected"
    )
    # A duplicate receipt protects the connection even if timestamps are old.
    SocialAccount.objects.filter(pk=replacement.pk).update(connected_at=timezone.now() - timedelta(days=1))
    second = post_callback(value=value)
    assert first.json() == second.json()
    assert SocialAccount.objects.filter(pk=replacement.pk).exists()
    assert ThreadsCallbackReceipt.objects.count() == 1


def test_old_callback_does_not_delete_new_connection(account):
    assert (
        post_callback(value=signed(issued_at=int((timezone.now() - timedelta(days=2)).timestamp()))).status_code == 200
    )
    assert SocialAccount.objects.filter(pk=account.pk).exists()


def test_different_app_secret_cannot_delete_account_with_same_id(account, settings):
    settings.PLATFORM_CREDENTIALS_FROM_ENV = {}
    PlatformCredential.objects.create(
        organization=account.workspace.organization,
        platform="threads",
        credentials={"app_id": "app-a", "app_secret": TEST_SECRET},
    )
    other_org = Organization.objects.create(name="Different app")
    PlatformCredential.objects.create(
        organization=other_org,
        platform="threads",
        credentials={"app_id": "app-b", "app_secret": "different-secret"},
    )
    other_workspace = Workspace.objects.create(name="Other workspace", organization=other_org)
    other_account = SocialAccount.objects.create(
        workspace=other_workspace, platform="threads", account_platform_id="123456", account_name="Other app account"
    )
    SocialAccount.objects.filter(pk=other_account.pk).update(connected_at=timezone.now() - timedelta(days=1))
    assert post_callback(value=signed(secret="different-secret")).status_code == 200
    assert SocialAccount.objects.filter(pk=account.pk).exists()
    assert not SocialAccount.objects.filter(pk=other_account.pk).exists()


def test_failed_deletion_rolls_back_receipt_and_account(account):
    with (
        patch("apps.social_accounts.threads_callbacks._remove_account", side_effect=RuntimeError("database error")),
        pytest.raises(RuntimeError),
    ):
        post_callback()
    assert SocialAccount.objects.filter(pk=account.pk).exists()
    assert ThreadsCallbackReceipt.objects.count() == 0


def test_unknown_user_is_acknowledged_without_data_change(account):
    assert post_callback(value=signed(user_id="999")).status_code == 200
    assert SocialAccount.objects.filter(pk=account.pk).exists()


def test_get_never_mutates(account):
    for name in ["threads_delete", "threads_deauthorize"]:
        assert Client().get(reverse(f"social_accounts:{name}")).status_code == 405
    assert SocialAccount.objects.filter(pk=account.pk).exists()


def test_receipt_does_not_store_raw_request(account):
    value = signed()
    post_callback(value=value)
    receipt = ThreadsCallbackReceipt.objects.get()
    assert receipt.request_digest == hashlib.sha256(f"delete\0{value}".encode()).hexdigest()
    assert receipt.action == "delete"


def test_second_precision_issue_time(account):
    issued_at = int(timezone.now().timestamp())
    SocialAccount.objects.filter(pk=account.pk).update(
        connected_at=timezone.datetime.fromtimestamp(issued_at + 0.1, tz=timezone.get_current_timezone())
    )
    assert post_callback(value=signed(issued_at=issued_at)).status_code == 200
    assert not SocialAccount.objects.filter(pk=account.pk).exists()
