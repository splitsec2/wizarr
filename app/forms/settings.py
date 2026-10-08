# app/forms/settings.py
from flask_babel import lazy_gettext as _l
from flask_wtf import FlaskForm
from wtforms import (
    BooleanField,
    IntegerField,
    PasswordField,
    SelectField,
    StringField,
)
from wtforms.validators import URL, DataRequired, Email, NumberRange, Optional


class SettingsForm(FlaskForm):
    server_type = SelectField(
        str(_l("Server Type")),
        choices=[
            ("plex", "Plex"),
            ("jellyfin", "Jellyfin"),
            ("emby", "Emby"),
            ("audiobookshelf", "Audiobookshelf"),
            ("drop", "Drop"),
            ("romm", "Romm"),
            ("komga", "Komga"),
            ("kavita", "Kavita"),
        ],
        validators=[DataRequired()],
    )
    server_name = StringField(str(_l("Server Name")), validators=[DataRequired()])
    server_url = StringField(str(_l("Server URL")), validators=[DataRequired()])
    api_key = StringField(str(_l("API Key")), validators=[Optional()])
    server_username = StringField(str(_l("RomM Username")), validators=[Optional()])
    server_password = StringField(str(_l("RomM Password")), validators=[Optional()])
    libraries = StringField(str(_l("Libraries")), validators=[Optional()])
    allow_downloads = BooleanField(
        str(_l("Allow Downloads")), default=False, validators=[Optional()]
    )
    allow_live_tv = BooleanField(
        str(_l("Allow Live TV")), default=False, validators=[Optional()]
    )
    overseerr_url = StringField(
        str(_l("Overseerr/Ombi URL")), validators=[Optional(), URL()]
    )
    ombi_api_key = StringField(str(_l("Ombi API Key")), validators=[Optional()])
    discord_id = StringField(str(_l("Discord ID")), validators=[Optional()])
    external_url = StringField(str(_l("External URL")), validators=[Optional()])

    # Universal download and live TV options (no longer server-specific)

    # Audiobookshelf specific
    allow_downloads_audiobookshelf = BooleanField(
        str(_l("Allow Downloads")), default=True, validators=[Optional()]
    )

    def __init__(self, install_mode: bool = False, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if install_mode:
            # During install wizard, libraries must be supplied
            self.libraries.validators = [DataRequired()]
            # api_key is mandatory for Plex/Jellyfin
            self.api_key.validators = [DataRequired()]


class EmailSettingsForm(FlaskForm):
    """Settings > Email: the SMTP server Wizarr sends mail through."""

    host = StringField(str(_l("SMTP server")), validators=[Optional()])
    port = IntegerField(
        str(_l("Port")), validators=[Optional(), NumberRange(min=1, max=65535)]
    )
    security = SelectField(
        str(_l("Encryption")),
        choices=[
            ("starttls", "STARTTLS"),
            ("ssl", "SSL/TLS"),
            ("none", str(_l("None"))),
        ],
        default="starttls",
    )
    username = StringField(str(_l("Username")), validators=[Optional()])
    # Never shown back; left empty it keeps the saved password.
    password = PasswordField(str(_l("Password")), validators=[Optional()])
    sender = StringField(str(_l("From address")), validators=[Optional(), Email()])


class EmailTestForm(FlaskForm):
    """Settings > Email: where to send a test message."""

    test_to = StringField(
        str(_l("Send a test email to")), validators=[DataRequired(), Email()]
    )
