"""
Views for user registration, login, logout, and account settings.
"""

from django.contrib import messages
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .forms import (
    CustomPasswordChangeForm,
    ProfileUpdateForm,
    UserLoginForm,
    UserRegistrationForm,
)


def register_view(request):
    """Create a new user account and sign the user in."""
    if request.user.is_authenticated:
        return redirect('dashboard')

    if request.method == 'POST':
        form = UserRegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(request, 'Account created successfully. Welcome!')
            return redirect('dashboard')
    else:
        form = UserRegistrationForm()

    return render(request, 'accounts/register.html', {'form': form})


def login_view(request):
    """Authenticate an existing user and redirect to the dashboard."""
    if request.user.is_authenticated:
        return redirect('dashboard')

    if request.method == 'POST':
        form = UserLoginForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            messages.success(request, f'Welcome back, {user.username}.')
            next_url = request.POST.get('next') or request.GET.get('next')
            if next_url and url_has_allowed_host_and_scheme(
                next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
            ):
                return redirect(next_url)
            return redirect('dashboard')
    else:
        form = UserLoginForm(request)

    return render(request, 'accounts/login.html', {'form': form, 'next': request.GET.get('next', '')})


@login_required
@require_POST
def logout_view(request):
    """Sign out the current user via POST request."""
    logout(request)
    messages.info(request, 'You have been signed out.')
    return redirect('accounts:login')


@login_required
def settings_view(request):
    """Allow users to update profile details or change their password."""
    profile_form = ProfileUpdateForm(instance=request.user)
    password_form = CustomPasswordChangeForm(user=request.user)

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'update_profile':
            profile_form = ProfileUpdateForm(request.POST, instance=request.user)
            if profile_form.is_valid():
                profile_form.save()
                messages.success(request, 'Profile details updated successfully.')
                return redirect('accounts:settings')
        elif action == 'change_password':
            password_form = CustomPasswordChangeForm(user=request.user, data=request.POST)
            if password_form.is_valid():
                user = password_form.save()
                # Keep the user logged in after changing their password.
                update_session_auth_hash(request, user)
                messages.success(request, 'Password changed successfully.')
                return redirect('accounts:settings')

    context = {
        'profile_form': profile_form,
        'password_form': password_form,
        'active_nav': 'settings',
    }
    return render(request, 'accounts/settings.html', context)
