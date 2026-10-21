import pytest
from django.contrib.auth import get_user_model


@pytest.mark.django_db
def test_dj_admin_login_is_the_only_public_admin_login(client):
    response = client.get('/dj-admin/login/')
    assert response.status_code == 200
    assert b'name="username"' in response.content


@pytest.mark.django_db
def test_legacy_django_admin_paths_are_not_exposed(client):
    assert client.get('/admin/').status_code == 404
    assert client.get('/admin/login/').status_code == 404


@pytest.mark.django_db
def test_staff_required_redirects_to_dj_admin_login(client):
    response = client.get('/dj-admin/')
    assert response.status_code == 302
    assert response.url == '/dj-admin/login/?next=/dj-admin/'


@pytest.mark.django_db
def test_login_redirects_authenticated_staff_to_dashboard(client):
    user = get_user_model().objects.create_user(
        username='dj', password='secret-test', is_staff=True,
    )
    client.force_login(user)
    response = client.get('/dj-admin/login/')
    assert response.status_code == 302
    assert response.url == '/dj-admin/'
