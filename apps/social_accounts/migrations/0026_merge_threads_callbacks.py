from django.db import migrations


class Migration(migrations.Migration):
    """Keep the deployed Threads receipt migration alongside upstream history."""

    dependencies = [
        ("social_accounts", "0019_threadscallbackreceipt"),
        ("social_accounts", "0025_youtuberevocation_and_more"),
    ]

    operations = []
