from django.urls import path

from . import threads_callbacks, views

app_name = "social_accounts"

urlpatterns = [
    path("threads/deauthorize/", threads_callbacks.deauthorize, name="threads_deauthorize"),
    path("threads/delete/", threads_callbacks.delete_data, name="threads_delete"),
    path(
        "threads/deletion-status/<uuid:receipt_id>/", threads_callbacks.deletion_status, name="threads_deletion_status"
    ),
    # Workspace-scoped views
    path(
        "<uuid:workspace_id>/",
        views.account_list,
        name="list",
    ),
    path(
        "<uuid:workspace_id>/connect/",
        views.connect_platform,
        name="connect",
    ),
    path(
        "<uuid:workspace_id>/connect/bluesky/",
        views.connect_bluesky,
        name="connect_bluesky",
    ),
    path(
        "<uuid:workspace_id>/connect/mastodon/",
        views.connect_mastodon,
        name="connect_mastodon",
    ),
    path(
        "<uuid:workspace_id>/connect/devto/",
        views.connect_devto,
        name="connect_devto",
    ),
    # OAuth callback (not workspace-scoped - platform redirects here)
    path(
        "callback/<str:platform>/",
        views.oauth_callback,
        name="oauth_callback",
    ),
    # Account selection (Facebook multi-page)
    path(
        "select-account/",
        views.select_account,
        name="select_account",
    ),
    # Per-account actions
    path(
        "<uuid:workspace_id>/<uuid:account_id>/reconnect/",
        views.reconnect,
        name="reconnect",
    ),
    path(
        "<uuid:workspace_id>/<uuid:account_id>/retry-webhooks/",
        views.retry_webhooks,
        name="retry_webhooks",
    ),
    path(
        "<uuid:workspace_id>/<uuid:account_id>/disconnect/",
        views.disconnect,
        name="disconnect",
    ),
]
