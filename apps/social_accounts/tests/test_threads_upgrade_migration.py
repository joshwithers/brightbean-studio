"""Upgrade the deployed fork without losing callback replay protection or tokens."""

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from apps.social_accounts.models import SocialAccount, ThreadsCallbackReceipt
from apps.workspaces.models import Workspace


@pytest.mark.django_db(transaction=True)
def test_upgrade_preserves_existing_threads_receipts_and_credentials(organization):
    workspace = Workspace.objects.create(name="Upgrade test", organization=organization)
    executor = MigrationExecutor(connection)
    heads = executor.loader.graph.leaf_nodes()
    previous = [("social_accounts", "0019_threadscallbackreceipt")]
    try:
        # Rewind both branches, then recreate the already-deployed fork schema.
        executor.migrate([("social_accounts", "0018_socialaccount_missing_scopes")])
        executor = MigrationExecutor(connection)
        executor.migrate(previous)
        historical = executor.loader.project_state(previous).apps
        receipt = historical.get_model("social_accounts", "ThreadsCallbackReceipt").objects.create(
            request_digest="a" * 64, action="delete"
        )
        account = historical.get_model("social_accounts", "SocialAccount").objects.create(
            workspace_id=workspace.pk,
            platform="threads",
            account_platform_id="upgrade-test-user",
            account_name="Existing Threads account",
            oauth_access_token="upgrade-test-token",
        )

        MigrationExecutor(connection).migrate(heads)

        assert ThreadsCallbackReceipt.objects.get(pk=receipt.pk).request_digest == "a" * 64
        upgraded = SocialAccount.objects.get(pk=account.pk)
        assert upgraded.oauth_access_token == "upgrade-test-token"
        assert upgraded.account_platform_id == "upgrade-test-user"
        assert upgraded.workspace_id == workspace.pk
        assert not MigrationExecutor(connection).loader.detect_conflicts()
    finally:
        MigrationExecutor(connection).migrate(heads)
