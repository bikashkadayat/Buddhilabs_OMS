from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("drafts", "0001_initial")]

    operations = [
        migrations.AlterField(
            model_name="documentdraft",
            name="kind",
            field=models.CharField(
                choices=[("memo", "Memo"), ("minute", "Minute"),
                         ("circular", "Circular"), ("task", "Task")],
                db_index=True, max_length=16),
        ),
    ]
