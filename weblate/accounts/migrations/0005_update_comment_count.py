# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

# Generated by Django 3.0.7 on 2020-08-02 12:13

from django.db import migrations
from django.db.models import Count


def update_counts(apps, schema_editor):
    Comment = apps.get_model("trans", "Comment")
    Profile = apps.get_model("accounts", "Profile")
    db_alias = schema_editor.connection.alias
    comments = Comment.objects.using(db_alias).values_list("user").annotate(Count("id"))
    for userid, count in comments:
        Profile.objects.using(db_alias).filter(user_id=userid).update(commented=count)


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0004_profile_commented"),
    ]

    operations = [
        migrations.RunPython(update_counts, migrations.RunPython.noop, elidable=True),
    ]
