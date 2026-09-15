"""Authenticated Threads app-removal and data-deletion callbacks.

Meta POSTs a base64url-encoded payload signed with the Threads app secret.
These endpoints never trust a browser session or an unsigned account ID.
"""

import base64
import binascii
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Count
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from apps.credentials.models import resolve_app_secret, resolve_app_secrets

from .models import SocialAccount, ThreadsCallbackReceipt


def _decode(value):
    return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)


def _verify(signed_request):
    if not isinstance(signed_request, str) or not 1 <= len(signed_request) <= 8192:
        raise ValueError("Invalid signed request")
    signature, payload = signed_request.split(".", 1)
    signature_bytes = _decode(signature)
    matched_secret = next(
        (
            secret
            for secret in resolve_app_secrets("threads")
            if hmac.compare_digest(
                signature_bytes,
                hmac.digest(secret.encode(), payload.encode("ascii"), "sha256"),
            )
        ),
        None,
    )
    if matched_secret is None:
        raise ValueError("Invalid signature")
    data = json.loads(_decode(payload))
    if not isinstance(data, dict) or data.get("algorithm") != "HMAC-SHA256":
        raise ValueError("Invalid algorithm")
    user_id = data.get("user_id")
    if not isinstance(user_id, str) or not user_id.isascii() or not user_id.isdecimal() or len(user_id) > 255:
        raise ValueError("Invalid user ID")
    issued_at = data.get("issued_at")
    now = timezone.now().timestamp()
    if type(issued_at) is not int or not 0 < issued_at <= now + 300:
        raise ValueError("Invalid issue time")
    expires = data.get("expires")
    if expires is not None and (type(expires) is not int or (expires != 0 and expires < now)):
        raise ValueError("Expired request")
    return user_id, datetime.fromtimestamp(issued_at, tz=UTC), matched_secret


def _remove_account(account):
    from apps.composer.models import PlatformPost, Post

    # Match Brightbean's disconnect behavior: preserve posts targeting another
    # account, remove posts that would otherwise become completely orphaned.
    orphan_post_ids = list(
        PlatformPost.objects.filter(social_account=account)
        .values("post_id")
        .annotate(total_platforms=Count("post__platform_posts"))
        .filter(total_platforms=1)
        .values_list("post_id", flat=True)
    )
    Post.objects.filter(id__in=orphan_post_ids).delete()
    # Cascades remove tokens, profile metadata, analytics and inbox records.
    # No request is sent to delete content already published on Threads.
    account.delete()


def _handle(request, action):
    signed_request = request.POST.get("signed_request", "")
    try:
        user_id, issued_at, secret = _verify(signed_request)
    except (ValueError, TypeError, OverflowError, UnicodeError, binascii.Error):
        return JsonResponse({"error": "Invalid signed request"}, status=400)

    digest = hashlib.sha256(f"{action}\0{signed_request}".encode()).hexdigest()
    with transaction.atomic():
        receipt, created = ThreadsCallbackReceipt.objects.get_or_create(
            request_digest=digest, defaults={"action": action}
        )
        if created:
            accounts = (
                SocialAccount.objects.select_for_update()
                .select_related("workspace")
                # Meta's issue time has second precision; database timestamps
                # include microseconds. Include connections in that second.
                .filter(
                    platform="threads", account_platform_id=user_id, connected_at__lt=issued_at + timedelta(seconds=1)
                )
            )
            for account in accounts:
                # An app-scoped ID is meaningful only within its own app. A
                # different organization's secret cannot delete this account.
                if hmac.compare_digest(secret, resolve_app_secret("threads", account.workspace.organization_id)):
                    _remove_account(account)

    if action == "delete":
        path = reverse("social_accounts:threads_deletion_status", kwargs={"receipt_id": receipt.id})
        return JsonResponse({"url": f"{settings.APP_URL.rstrip('/')}{path}", "confirmation_code": receipt.id.hex})
    return JsonResponse({"success": True})


@csrf_exempt
@require_POST
def deauthorize(request):
    return _handle(request, "deauthorize")


@csrf_exempt
@require_POST
def delete_data(request):
    return _handle(request, "delete")


@require_GET
def deletion_status(request, receipt_id):
    receipt = get_object_or_404(ThreadsCallbackReceipt, id=receipt_id, action="delete")
    response = HttpResponse(
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>Threads data deletion complete</title><main>"
        "<h1>Threads data deletion complete</h1>"
        "<p>The matching Threads connection and its associated data have been removed from this application. "
        "If no matching connection was stored, no action was needed. "
        "Content already published on Threads is managed through Threads.</p>"
        f"<p>Confirmation code: {receipt.id.hex}</p></main></html>"
    )
    response["Cache-Control"] = "no-store"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response
