from uuid import uuid4

from django import forms
from django.core.exceptions import ValidationError

from .models import TrainingSession
from .parameters import PISTOL_DISTANCES, resolve_parameters
from .session_creation import validated_parameters


class CreationContextField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f"{obj.participant.full_name} · {obj.activity.name} · {obj.participant_identifier or '—'}"


class SessionCreationForm(forms.Form):
    participation = CreationContextField(queryset=None, label="Peserta dan kegiatan", empty_label="Pilih konteks peserta")
    weapon_type = forms.ChoiceField(
        label="Jenis senjata", choices=(("", "Pilih jenis senjata"), *TrainingSession.WeaponType.choices),
    )
    shooting_position = forms.ChoiceField(
        label="Sikap untuk laras panjang", required=False,
        choices=(("", "Pilih sikap"), *TrainingSession.ShootingPosition.choices),
    )
    distance_m = forms.ChoiceField(
        label="Jarak untuk pistol", required=False,
        choices=(("", "Pilih jarak"), *((str(value), f"{value:g} m") for value in PISTOL_DISTANCES)),
    )
    reason = forms.CharField(label="Alasan pembuatan sesi", max_length=2000,
                             widget=forms.Textarea(attrs={"rows": 3}))
    operation_id = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, *args, contexts, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["participation"].queryset = contexts
        self.initial.setdefault("operation_id", uuid4())

    def clean(self):
        cleaned = super().clean()
        if set(self.data) - set(self.fields) - {"csrfmiddlewaretoken"}:
            self.add_error(None, "Permintaan memuat field yang tidak diizinkan.")
        if hasattr(self.data, "getlist") and any(len(self.data.getlist(key)) > 1 for key in self.fields):
            self.add_error(None, "Field permintaan tidak boleh berulang.")
        if "weapon_type" in cleaned:
            try:
                self.parameters = validated_parameters({
                    key: cleaned.get(key) for key in ("weapon_type", "shooting_position", "distance_m")
                })
            except ValidationError as error:
                if hasattr(error, "error_dict"):
                    for key, errors in error.error_dict.items():
                        self.add_error(key if key in self.fields else None, errors)
                else:
                    self.add_error(None, error)
        return cleaned


def automatic_parameter_info():
    return {
        "long_gun": resolve_parameters(weapon_type="laras_panjang", shooting_position="berdiri"),
        "pistol": resolve_parameters(weapon_type="pistol", distance_m=PISTOL_DISTANCES[0]),
    }
