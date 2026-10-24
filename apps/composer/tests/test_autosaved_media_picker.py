"""A library picker opened before autosave must attach to the resulting draft."""

import json
import re
from html.parser import HTMLParser

from django.urls import reverse

from apps.accounts.models import User
from apps.composer.models import PlatformPost, Post, PostMedia
from apps.media_library.models import MediaAsset
from apps.members.models import WorkspaceMembership
from apps.social_accounts.models import SocialAccount
from apps.workspaces.models import Workspace

from .test_unsplash import ComposerTestCase


class PickerParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.choices = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "hx-post" in attrs and "media_asset_id" in attrs.get("hx-vals", ""):
            self.choices.append(attrs)


class AutosavedMediaPickerTests(ComposerTestCase):
    def setUp(self):
        super().setUp()
        self.account = SocialAccount.objects.create(
            workspace=self.workspace,
            platform="pinterest",
            account_platform_id="pinterest-test",
            account_name="Test Pinterest",
        )
        self.asset = MediaAsset.objects.create(
            organization=self.org,
            workspace=self.workspace,
            file="test/wedding.jpg",
            filename="wedding.jpg",
            media_type=MediaAsset.MediaType.IMAGE,
            mime_type="image/jpeg",
        )
        self.pending_url = reverse("composer:attach_pending_media", kwargs={"workspace_id": self.workspace.id})
        self.data = {
            "title": "A wedding story",
            "caption": "A little wedding magic.",
            "selected_accounts": str(self.account.pk),
        }

    def _autosave(self):
        response = self.client.post(reverse("composer:autosave", kwargs={"workspace_id": self.workspace.id}), self.data)
        self.assertEqual(response.status_code, 200)
        return Post.objects.get(pk=json.loads(response["HX-Trigger"])["autosaved"]["postId"])

    def _attach(self, post_id):
        return self.client.post(
            self.pending_url,
            {
                "media_asset_id": str(self.asset.pk),
                "_autosave_post_id": str(post_id),
            },
        )

    def test_loaded_picker_uses_current_autosaved_post_and_can_schedule_pinterest(self):
        # Load the original picker before the autosave exists. Its rendered
        # request configuration must include the hidden field's *current*
        # value when the user later clicks the image.
        response = self.client.get(reverse("composer:media_picker", kwargs={"workspace_id": self.workspace.id}))
        self.assertEqual(response.status_code, 200)
        parser = PickerParser()
        parser.feed(response.content.decode())
        choice = parser.choices[0]
        post = self._autosave()
        current_form = {"_autosave_post_id": str(post.pk)}
        data = json.loads(choice["hx-vals"])
        for name in re.findall(r"\[name=['\"]([^'\"]+)['\"]\]", choice.get("hx-include", "")):
            data[name] = current_form[name]

        response = self.client.post(choice["hx-post"], data)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(PostMedia.objects.filter(post=post, media_asset=self.asset).exists())
        self.assertNotIn(f"pending_media_{self.workspace.id}", self.client.session)
        response = self.client.post(
            reverse("composer:save_post_edit", kwargs={"workspace_id": self.workspace.id, "post_id": post.id}),
            {
                **self.data,
                "action": "publish_now",
                f"pin_board_id_{self.account.pk}": "board-1",
                f"pin_link_url_{self.account.pk}": "https://example.com/wedding",
            },
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 204)
        platform_post = PlatformPost.objects.get(post=post, social_account=self.account)
        self.assertEqual(platform_post.status, PlatformPost.Status.SCHEDULED)
        self.assertIsNotNone(platform_post.scheduled_at)
        self.assertEqual(platform_post.platform_extra["board_id"], "board-1")

    def test_before_first_autosave_keeps_pending_media_flow(self):
        response = self._attach("")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.session[f"pending_media_{self.workspace.id}"], [str(self.asset.pk)])
        post = self._autosave()
        self.assertTrue(PostMedia.objects.filter(post=post, media_asset=self.asset).exists())

    def test_repeated_selection_does_not_duplicate_attachment(self):
        post = self._autosave()
        self.assertEqual(self._attach(post.pk).status_code, 200)
        self.assertEqual(self._attach(post.pk).status_code, 200)
        self.assertEqual(PostMedia.objects.filter(post=post, media_asset=self.asset).count(), 1)

    def test_rejects_post_in_another_workspace(self):
        other = Workspace.objects.create(organization=self.org, name="Other workspace")
        post = Post.objects.create(workspace=other, author=self.user, caption="Other draft")
        self.assertEqual(self._attach(post.pk).status_code, 404)
        self.assertFalse(PostMedia.objects.exists())

    def test_contributor_cannot_attach_to_someone_elses_draft(self):
        other = User.objects.create_user(email="other@example.com", password="local-only")
        post = Post.objects.create(workspace=self.workspace, author=other, caption="Other author's draft")
        WorkspaceMembership.objects.filter(user=self.user, workspace=self.workspace).update(
            workspace_role="contributor"
        )
        self.assertEqual(self._attach(post.pk).status_code, 403)
        self.assertFalse(PostMedia.objects.exists())

    def test_rejects_published_post_and_invalid_id(self):
        post = self._autosave()
        PlatformPost.objects.filter(post=post).update(status=PlatformPost.Status.PUBLISHED)
        self.assertEqual(self._attach(post.pk).status_code, 400)
        self.assertEqual(self._attach("invalid").status_code, 400)
        self.assertFalse(PostMedia.objects.exists())
