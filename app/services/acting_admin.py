"""Which admin account is behind this request, for an invite's "created by".

A signed-in admin account (local, LDAP or Cloudflare Access) acts as itself. An
API-key request acts as the admin who created the key. The legacy shared admin
(``DISABLE_BUILTIN_AUTH`` without Cloudflare Access) is nobody in particular.
"""

from flask import has_request_context, request
from flask_login import current_user

# Kept on the request itself (its WSGI environ), never on ``g``, which belongs
# to the app context and can outlive a request.
_API_KEY_CREATOR = "wizarr.api_key_creator_id"


def remember_api_key(api_key) -> None:
    """Record whose API key authenticated this request."""
    request.environ[_API_KEY_CREATOR] = api_key.created_by_id


def acting_admin():
    """The AdminAccount making this request, or None."""
    if not has_request_context():
        return None
    from app.extensions import db
    from app.models import AdminAccount

    if current_user.is_authenticated and isinstance(current_user, AdminAccount):
        return db.session.get(AdminAccount, current_user.id)
    creator_id = request.environ.get(_API_KEY_CREATOR)
    if creator_id is not None:
        return db.session.get(AdminAccount, creator_id)
    return None
