"""Google's v1 location reads and v4 local-post writes use different paths."""

from unittest.mock import patch

import httpx
import pytest

from providers.google_business import GoogleBusinessProvider
from providers.types import PostType, PublishContent


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
