"""
Forms for user registration, login, and account settings.
"""

from django import forms
from django.contrib.auth.forms import (
    AuthenticationForm,
    PasswordChangeForm,
    UserCreationForm,
)
from django.contrib.auth.models import User

INPUT_CLASS = (
    'block w-full rounded-lg border border-slate-300 bg-white px-3.5 py-2.5 '
    'text-sm text-slate-900 placeholder-slate-400 shadow-xs '
    'focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/20'
)


def _apply_tailwind_classes(form_fields):
    """Apply consistent Tailwind CSS styling to all visible form inputs."""
    for field in form_fields.values():
        existing = field.widget.attrs.get('class', '')
        field.widget.attrs['class'] = f'{INPUT_CLASS} {existing}'.strip()


class UserRegistrationForm(UserCreationForm):
    """Registration form extending Django's built-in UserCreationForm."""

    first_name = forms.CharField(max_length=150, required=True)
    last_name = forms.CharField(max_length=150, required=True)
    email = forms.EmailField(required=True)

    class Meta:
        model = User
        fields = ('username', 'first_name', 'last_name', 'email', 'password1', 'password2')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_tailwind_classes(self.fields)
        self.fields['username'].widget.attrs['placeholder'] = 'Choose a username'
        self.fields['first_name'].widget.attrs['placeholder'] = 'First name'
        self.fields['last_name'].widget.attrs['placeholder'] = 'Last name'
        self.fields['email'].widget.attrs['placeholder'] = 'you@example.com'

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError('An account with this email address already exists.')
        return email


class UserLoginForm(AuthenticationForm):
    """Login form with Tailwind CSS styling."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_tailwind_classes(self.fields)
        self.fields['username'].widget.attrs['placeholder'] = 'Enter your username'
        self.fields['password'].widget.attrs['placeholder'] = 'Enter your password'


class ProfileUpdateForm(forms.ModelForm):
    """Form allowing users to update their name and email."""

    email = forms.EmailField(required=True)

    class Meta:
        model = User
        fields = ('first_name', 'last_name', 'email')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_tailwind_classes(self.fields)

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        existing = User.objects.filter(email__iexact=email).exclude(pk=self.instance.pk)
        if existing.exists():
            raise forms.ValidationError('Another account already uses this email address.')
        return email


class CustomPasswordChangeForm(PasswordChangeForm):
    """Password change form with Tailwind CSS styling."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_tailwind_classes(self.fields)
