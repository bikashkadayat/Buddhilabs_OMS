import pytest
from rest_framework.test import APIClient

from users.models import User


@pytest.fixture
def api():
    return APIClient()


@pytest.fixture
def author(db):
    return User.objects.create_user(
        username="draft.author", email="draft.author@example.test",
        password="pw", first_name="Draft", last_name="Author")


@pytest.fixture
def other(db):
    return User.objects.create_user(
        username="draft.other", email="draft.other@example.test",
        password="pw", first_name="Other", last_name="Person")


@pytest.fixture
def payload():
    """A snapshot shaped the way the client actually sends one."""
    return {
        "form": {"subject": "Server refresh approval", "to_line": "CEO"},
        "sections": [{"title": "Background", "body": "<p>Ageing hardware.</p>"}],
    }
