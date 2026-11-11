"""Presentation only; credential checking remains Django's AuthenticationForm."""

from django.contrib.auth.forms import AuthenticationForm


class OfficerAuthenticationForm(AuthenticationForm):
    error_messages = {
        **AuthenticationForm.error_messages,
        "invalid_login": "Email atau kata sandi tidak valid.",
        "inactive": "Email atau kata sandi tidak valid.",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].label = "Email"
        self.fields["username"].widget.attrs.update({
            "autocomplete": "username",
            "inputmode": "email",
            "autocapitalize": "none",
            "spellcheck": "false",
        })
        self.fields["password"].label = "Kata sandi"
