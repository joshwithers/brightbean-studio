"""Google's v1 location reads and v4 local-post writes use different paths."""

from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

from providers.google_business import GoogleBusinessProvider
from providers.types import PostType, PublishContent


@pytest.mark.parametrize(
    "location, expected_account",
    [("locations/456", "accounts/123"), ("accounts/789/locations/456", "accounts/789")],
)
def test_publisher_uses_the_connected_listing_instead_of_the_deployment_default(location, expected_account):
    from apps.publisher.engine import _resolve_publish_credentials

    account = SimpleNamespace(
        platform="google_business",
        account_platform_id=location,
        workspace=SimpleNamespace(organization_id="org"),
    )
    defaults = {"account_id": "accounts/123", "location_id": "locations/another-business"}
    with patch("apps.publisher.engine.resolve_platform_credentials", return_value=dict(defaults)):
        credentials = _resolve_publish_credentials(account)

    assert credentials["location_id"] == location
    assert credentials["account_id"] == expected_account


def test_profile_requests_required_location_fields():
    provider = GoogleBusinessProvider({"account_id": "accounts/123"})
    response = httpx.Response(
        200,
        json={
            "locations": [
                {
                    "name": "locations/456",
                    "title": "Wedding Celebrant",
                    "storefrontAddress": {"addressLines": ["Example street"]},
                    "phoneNumbers": {"primaryPhone": "+61 400 000 000"},
                }
            ]
        },
    )
    with patch.object(provider, "_request", return_value=response) as request:
        profile = provider.get_profile("token")

    assert request.call_args.kwargs["params"]["readMask"] == "name,title,storefrontAddress,phoneNumbers"
    assert profile.name == "Wedding Celebrant"
    assert profile.extra == {"address": "Example street", "phone": "+61 400 000 000"}


@pytest.mark.parametrize("location_id", ["456", "locations/456", "accounts/123/locations/456"])
def test_profile_reads_the_configured_publishing_location(location_id):
    provider = GoogleBusinessProvider({"account_id": "accounts/123", "location_id": location_id})
    response = httpx.Response(200, json={"name": "locations/456", "title": "Selected Wedding Celebrant"})

    with patch.object(provider, "_request", return_value=response) as request:
        profile = provider.get_profile("token")

    request.assert_called_once_with(
        "GET",
        "https://mybusinessbusinessinformation.googleapis.com/v1/locations/456",
        access_token="token",
        params={"readMask": "name,title,storefrontAddress,phoneNumbers"},
    )
    assert profile.platform_id == "locations/456"
    assert profile.name == "Selected Wedding Celebrant"


def test_publish_converts_v1_location_to_v4_parent():
    provider = GoogleBusinessProvider({"account_id": "accounts/123"})
    responses = [
        httpx.Response(200, json={"locations": [{"name": "locations/456"}]}),
        httpx.Response(200, json={"name": "accounts/123/locations/456/localPosts/789"}),
    ]
    with patch.object(provider, "_request", side_effect=responses) as request:
        result = provider.publish_post("token", PublishContent(text="Our latest article", post_type=PostType.TEXT))

    location_read, post_write = request.call_args_list
    assert location_read.kwargs["params"]["readMask"] == "name,title,storefrontAddress,phoneNumbers"
    assert post_write.args == (
        "POST",
        "https://mybusiness.googleapis.com/v4/accounts/123/locations/456/localPosts",
    )
    assert post_write.kwargs["json"]["summary"] == "Our latest article"
    assert result.platform_post_id == "accounts/123/locations/456/localPosts/789"


def test_publish_includes_the_articles_learn_more_link():
    provider = GoogleBusinessProvider({"account_id": "accounts/123", "location_id": "locations/456"})
    response = httpx.Response(200, json={"name": "accounts/123/locations/456/localPosts/789"})
    content = PublishContent(text="A ceremony should feel alive.", link_url="https://example.com/ceremonies")

    with patch.object(provider, "_request", return_value=response) as request:
        provider.publish_post("token", content)

    assert request.call_args.kwargs["json"]["callToAction"] == {
        "actionType": "LEARN_MORE",
        "url": "https://example.com/ceremonies",
    }


def test_publish_without_a_link_keeps_a_plain_update():
    provider = GoogleBusinessProvider({"account_id": "accounts/123", "location_id": "locations/456"})
    response = httpx.Response(200, json={"name": "accounts/123/locations/456/localPosts/789"})
    with patch.object(provider, "_request", return_value=response) as request:
        provider.publish_post("token", PublishContent(text="A plain update"))

    assert "callToAction" not in request.call_args.kwargs["json"]


@pytest.mark.parametrize("location_id", ["456", "locations/456", "accounts/123/locations/456"])
def test_publish_accepts_configured_location_resource_forms(location_id):
    provider = GoogleBusinessProvider({"account_id": "accounts/123", "location_id": location_id})
    response = httpx.Response(200, json={"name": "accounts/123/locations/456/localPosts/789"})
    with patch.object(provider, "_request", return_value=response) as request:
        provider.publish_post("token", PublishContent(text="Our latest article", post_type=PostType.TEXT))

    request.assert_called_once()
    assert request.call_args.args == (
        "POST",
        "https://mybusiness.googleapis.com/v4/accounts/123/locations/456/localPosts",
    )
