"""
Automated tests for Phase 1 authentication, route protection, and dashboard access.
"""

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse


class AuthenticationAndDashboardTests(TestCase):
    """Verify registration, login, logout, settings, and protected dashboard routes."""

    def setUp(self):
        self.user_password = 'StrongTestPass!2026'
        self.user = User.objects.create_user(
            username='researcher1',
            email='researcher1@example.com',
            first_name='Asha',
            last_name='Patel',
            password=self.user_password,
        )

    def test_protected_routes_redirect_anonymous_users(self):
        protected_urls = [
            reverse('dashboard'),
            reverse('datasets:list'),
            reverse('datasets:explorer'),
            reverse('datasets:cleaning'),
            reverse('analytics:analysis'),
            reverse('analytics:visualizations'),
            reverse('analytics:anomalies'),
            reverse('forecasting:home'),
            reverse('forecasting:experiments'),
            reverse('forecasting:compare'),
            reverse('reports:list'),
            reverse('accounts:settings'),
        ]
        login_url = reverse('accounts:login')
        for url in protected_urls:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response.url.startswith(login_url))

    def test_user_registration_and_duplicate_email_validation(self):
        register_url = reverse('accounts:register')
        response = self.client.post(
            register_url,
            {
                'username': 'student_dev',
                'first_name': 'Rohan',
                'last_name': 'Sharma',
                'email': 'rohan@example.com',
                'password1': 'MScProjectPass!99',
                'password2': 'MScProjectPass!99',
            },
        )
        self.assertRedirects(response, reverse('dashboard'))
        self.assertTrue(User.objects.filter(username='student_dev').exists())

        # Log out and attempt duplicate email registration
        self.client.post(reverse('accounts:logout'))
        dup_response = self.client.post(
            register_url,
            {
                'username': 'another_user',
                'first_name': 'Test',
                'last_name': 'User',
                'email': 'ROHAN@example.com',
                'password1': 'MScProjectPass!99',
                'password2': 'MScProjectPass!99',
            },
        )
        self.assertEqual(dup_response.status_code, 200)
        self.assertContains(dup_response, 'already exists')

    def test_login_and_open_redirect_protection(self):
        login_url = reverse('accounts:login')
        response = self.client.post(
            login_url,
            {
                'username': 'researcher1',
                'password': self.user_password,
                'next': '//evil.example.com',
            },
        )
        self.assertRedirects(response, reverse('dashboard'))

    def test_authenticated_user_can_access_all_workspace_pages(self):
        self.client.login(username='researcher1', password=self.user_password)
        workspace_urls = [
            reverse('dashboard'),
            reverse('datasets:list'),
            reverse('datasets:explorer'),
            reverse('datasets:cleaning'),
            reverse('analytics:analysis'),
            reverse('analytics:visualizations'),
            reverse('analytics:anomalies'),
            reverse('forecasting:home'),
            reverse('forecasting:experiments'),
            reverse('forecasting:compare'),
            reverse('reports:list'),
            reverse('accounts:settings'),
        ]
        for url in workspace_urls:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, f'Failed loading {url}')

    def test_logout_requires_post_and_logs_out(self):
        self.client.login(username='researcher1', password=self.user_password)
        get_response = self.client.get(reverse('accounts:logout'))
        self.assertEqual(get_response.status_code, 405)

        post_response = self.client.post(reverse('accounts:logout'))
        self.assertRedirects(post_response, reverse('accounts:login'))
        dash_response = self.client.get(reverse('dashboard'))
        self.assertEqual(dash_response.status_code, 302)

    def test_profile_update_and_password_change(self):
        self.client.login(username='researcher1', password=self.user_password)
        settings_url = reverse('accounts:settings')

        profile_res = self.client.post(
            settings_url,
            {
                'action': 'update_profile',
                'first_name': 'Asha',
                'last_name': 'Verma',
                'email': 'asha.verma@example.com',
            },
        )
        self.assertRedirects(profile_res, settings_url)
        self.user.refresh_from_db()
        self.assertEqual(self.user.last_name, 'Verma')
        self.assertEqual(self.user.email, 'asha.verma@example.com')

        new_pw = 'UpdatedStrongPass!2026'
        pw_res = self.client.post(
            settings_url,
            {
                'action': 'change_password',
                'old_password': self.user_password,
                'new_password1': new_pw,
                'new_password2': new_pw,
            },
        )
        self.assertRedirects(pw_res, settings_url)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(new_pw))
