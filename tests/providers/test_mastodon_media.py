"""Upload compatibility without changing the original library image."""

import io
import shutil
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from PIL import Image

from providers.exceptions import PublishError
from providers.mastodon import MastodonProvider


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is installed in the production image")
def test_avif_upload_is_a_valid_jpeg_and_original_is_unchanged():
    source = Path(__file__).parents[1] / "fixtures/mastodon-still.avif"
    original = source.read_bytes()
    uploaded_paths = []
    provider = MastodonProvider({"instance_url": "https://example.social"})

    def receive_upload(*args, **kwargs):
        uploaded = kwargs["files"]["file"]
        uploaded_paths.append(Path(uploaded.name))
        image = Image.open(io.BytesIO(uploaded.read()))
        assert image.format == "JPEG"
        assert image.size == (64, 64)
        image.load()
        return httpx.Response(200, json={"id": "media-123"})

    with patch.object(provider, "_request", side_effect=receive_upload) as request:
        assert provider._upload_media("token", str(source)) == "media-123"

    assert request.call_count == 1
    assert source.read_bytes() == original
    assert not uploaded_paths[0].exists()


def test_conversion_failure_does_not_upload_or_retry(tmp_path):
    source = tmp_path / "broken.avif"
    source.write_bytes(b"invalid image")
    provider = MastodonProvider({"instance_url": "https://example.social"})
    with (
        patch("providers.mastodon.subprocess.run", side_effect=OSError("converter unavailable")),
        patch.object(provider, "_request") as request,
        pytest.raises(PublishError) as error,
    ):
        provider._upload_media("token", str(source))
    assert error.value.retryable is False
    request.assert_not_called()


def test_existing_formats_are_uploaded_without_conversion(tmp_path):
    source = tmp_path / "original.png"
    Image.new("RGB", (10, 10), "blue").save(source)
    provider = MastodonProvider({"instance_url": "https://example.social"})
    with (
        patch("providers.mastodon.subprocess.run") as converter,
        patch.object(provider, "_request", return_value=httpx.Response(200, json={"id": "media-1"})),
    ):
        assert provider._upload_media("token", str(source)) == "media-1"
    converter.assert_not_called()
