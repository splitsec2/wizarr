"""The protected-address list (Settings > General) as an account guard rule.

``app.services.protected`` keeps the list; this makes the same list refuse
delete, switch off and remove everywhere, not only in the expiry sweeps. A build
without ``app.services.protected`` has no list, so the rule does nothing.
"""

from app.models import User
from app.services.account_guard import register


def address_rule(user: User) -> str | None:
    try:
        from app.services.protected import is_protected_user
    except ImportError:
        return None
    if is_protected_user(user):
        return "This address is on the protected list."
    return None


register(address_rule)
