# Generated by Django 4.2.20 on 2025-04-27 20:46

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0009_property_featured_alter_property_floor_plan_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="property",
            name="premium",
            field=models.BooleanField(default=False),
        ),
    ]
