from django.db import migrations


def create_homepage(apps, schema_editor):
    ContentType = apps.get_model("contenttypes.ContentType")
    Page = apps.get_model("wagtailcore.Page")
    Site = apps.get_model("wagtailcore.Site")
    HomePage = apps.get_model("home.HomePage")

    # Delete the default "Welcome to your new Wagtail site!" page (id=2).
    Page.objects.filter(id=2).delete()

    homepage_content_type, __ = ContentType.objects.get_or_create(
        model="homepage", app_label="home"
    )

    homepage = HomePage.objects.create(
        title="Home",
        draft_title="Home",
        slug="home",
        content_type=homepage_content_type,
        path="00010001",
        depth=2,
        numchild=0,
        url_path="/home/",
    )

    # Point the default site at the new homepage.
    Site.objects.create(
        hostname="localhost", root_page=homepage, is_default_site=True
    )


def remove_homepage(apps, schema_editor):
    HomePage = apps.get_model("home.HomePage")
    HomePage.objects.filter(slug="home").delete()
    ContentType = apps.get_model("contenttypes.ContentType")
    ContentType.objects.filter(model="homepage", app_label="home").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("home", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_homepage, remove_homepage),
    ]
