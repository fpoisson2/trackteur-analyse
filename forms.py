from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import (
    StringField,
    PasswordField,
    IntegerField,
    FloatField,
    HiddenField,
    SelectField,
    BooleanField,
)
from wtforms.validators import (
    DataRequired,
    Length,
    Optional,
    NumberRange,
    URL,
)


class LocalizedFloatField(FloatField):
    """Float field that accepts French comma decimals (e.g., '25,0')."""

    def process_formdata(self, valuelist):  # type: ignore[override]
        if valuelist:
            valuelist = [
                (v.replace(",", ".") if isinstance(v, str) else v)
                for v in valuelist
            ]
        super().process_formdata(valuelist)


class LoginForm(FlaskForm):
    username = StringField(
        "Nom d’utilisateur",
        validators=[DataRequired(message="Nom d’utilisateur requis"), Length(min=3, max=64, message="Doit faire entre 3 et 64 caractères")],
    )
    password = PasswordField(
        "Mot de passe",
        validators=[DataRequired(message="Mot de passe requis"), Length(min=3, max=128, message="Doit faire au moins 3 caractères")],
    )


class AdminConfigForm(FlaskForm):
    base_url = StringField(
        "Adresse du serveur",
        validators=[Optional(), URL(message="URL invalide")],
    )
    token_global = StringField(
        "Token API",
        validators=[Optional(), Length(min=3, max=256, message="Le token doit faire au moins 3 caractères")],
    )
    analysis_hour = IntegerField(
        "Heure d'analyse",
        validators=[Optional(), NumberRange(min=0, max=23, message="Doit être entre 0 et 23")],
    )
    eps_meters = LocalizedFloatField(
        "Distance de clustering",
        validators=[Optional(), NumberRange(min=1, max=10000, message="Doit être >= 1")],
    )
    min_surface = LocalizedFloatField(
        "Surface minimale",
        validators=[Optional(), NumberRange(min=0, max=100000, message="Doit être >= 0")],
    )
    alpha_shape = LocalizedFloatField(
        "Paramètre alpha",
        validators=[Optional(), NumberRange(min=0, max=10, message="Doit être >= 0")],
    )
    # equipment ids are handled as free checkboxes in template; validated in view


class AddUserForm(FlaskForm):
    action = HiddenField(default="add")
    username = StringField(
        "Nom d’utilisateur",
        validators=[DataRequired(message="Nom requis"), Length(min=3, max=64, message="3–64 caractères")],
    )
    password = PasswordField(
        "Mot de passe",
        validators=[DataRequired(message="Mot de passe requis"), Length(min=3, max=128, message="Au moins 3 caractères")],
    )
    role = StringField(
        "Rôle",
        validators=[DataRequired(message="Rôle requis")],
    )


class ResetPasswordForm(FlaskForm):
    action = HiddenField(default="reset")
    user_id = IntegerField("ID utilisateur", validators=[DataRequired()])
    password = PasswordField(
        "Nouveau mot de passe",
        validators=[DataRequired(message="Mot de passe requis"), Length(min=3, max=128, message="Au moins 3 caractères")],
    )


class DeleteUserForm(FlaskForm):
    action = HiddenField(default="delete")
    user_id = IntegerField("ID utilisateur", validators=[DataRequired()])


class ProviderForm(FlaskForm):
    name = StringField(
        "Nom du fournisseur",
        validators=[DataRequired(message="Nom requis"), Length(min=2, max=64)],
    )
    token = StringField(
        "Token API",
        validators=[
            DataRequired(message="Token requis"),
            Length(min=3, max=256),
        ],
    )
    orgid = StringField(
        "Organization ID",
        validators=[Optional(), Length(min=1, max=64)],
    )


class SimAssociationForm(FlaskForm):
    equipment_id = HiddenField(validators=[DataRequired()])
    provider = SelectField(
        "Fournisseur",
        coerce=int,
        validators=[DataRequired(message="Fournisseur requis")],
    )
    sim = SelectField(
        "Carte SIM",
        coerce=str,
        validators=[DataRequired(message="SIM requise")],
        choices=[],
        validate_choice=False,
    )


class UpdateForm(FlaskForm):
    """Formulaire permettant de choisir la version à mettre à jour."""

    version = SelectField("Version", choices=[], validators=[DataRequired()])
    include_prerelease = BooleanField("Mode bêta testeur")
