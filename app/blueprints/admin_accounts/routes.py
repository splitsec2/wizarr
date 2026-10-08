import re

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_babel import _
from flask_login import current_user, login_required

from app.blueprints.admin.routes import admin_bp
from app.extensions import db
from app.forms.admin import AccessAdminCreateForm, AdminCreateForm, AdminUpdateForm
from app.forms.validators import USERNAME_MAX_LENGTH, USERNAME_MIN_LENGTH
from app.models import AdminAccount, WebAuthnCredential
from app.services import cloudflare_access

admin_accounts_bp = Blueprint("admin_accounts", __name__, url_prefix="/settings/admins")


def _link_access_email(acc: AdminAccount, email: str | None, field) -> bool:
    """Link *acc* to a Cloudflare Access email, or unlink it when blank.

    Returns False, with an error on *field*, when the email can't be used.
    """
    email = (email or "").strip().lower() or None
    if email is None:
        if acc.auth_source == cloudflare_access.AUTH_SOURCE:
            acc.auth_source = "local"
            acc.external_id = None
        return True
    if acc.external_id and acc.auth_source != cloudflare_access.AUTH_SOURCE:
        field.errors = [*list(field.errors), _("This admin signs in through LDAP.")]
        return False
    other = AdminAccount.query.filter_by(
        auth_source=cloudflare_access.AUTH_SOURCE, external_id=email
    ).first()
    if other is not None and other.id != acc.id:
        field.errors = [
            *list(field.errors),
            _("Another admin already signs in with this email."),
        ]
        return False
    acc.auth_source = cloudflare_access.AUTH_SOURCE
    acc.external_id = email
    return True


def _username_from_email(email: str) -> str:
    """A free username built from the email's local part."""
    base = re.sub(r"[^\w'.-]", "-", email.split("@", 1)[0])
    if len(base) < USERNAME_MIN_LENGTH:
        base = f"{base}-admin"
    base = base[:USERNAME_MAX_LENGTH]
    name, n = base, 2
    while AdminAccount.query.filter_by(username=name).first() is not None:
        suffix = f"-{n}"
        name = base[: USERNAME_MAX_LENGTH - len(suffix)] + suffix
        n += 1
    return name


@admin_accounts_bp.route("", methods=["GET"])
@login_required
def list_admins():
    """Render list of admin accounts."""
    admins = AdminAccount.query.order_by(AdminAccount.username).all()
    if request.headers.get("HX-Request"):
        return render_template("settings/admins.html", admins=admins)
    return render_template("settings/admins.html", admins=admins)


# ── Create ─────────────────────────────────────────────────────────────
@admin_accounts_bp.route("/create", methods=["GET", "POST"])
@login_required
def create_admin():
    if cloudflare_access.enabled():
        return _create_access_admin()
    form = AdminCreateForm()
    if form.validate_on_submit():
        if AdminAccount.query.filter_by(username=form.username.data).first():
            form.username.errors = [
                *list(form.username.errors),
                "Username already exists.",
            ]
        else:
            acc = AdminAccount()
            acc.username = form.username.data
            acc.display_name = form.display_name.data or None
            if form.password.data:
                acc.set_password(form.password.data)
            db.session.add(acc)
            db.session.commit()
            flash(_("Admin created"), "success")
            return redirect(url_for("admin_accounts.list_admins"))
    # GET or POST-with-errors: render modal
    return render_template("modals/create-admin.html", form=form)


def _create_access_admin():
    """Under Cloudflare Access an admin is added by their Access email; they
    sign in through Access, so the account has no password."""
    form = AccessAdminCreateForm()
    if form.validate_on_submit():
        acc = AdminAccount()
        if _link_access_email(acc, form.access_email.data, form.access_email):
            acc.username = _username_from_email(acc.external_id)
            acc.display_name = form.display_name.data or None
            db.session.add(acc)
            db.session.commit()
            flash(_("Admin created"), "success")
            return redirect(url_for("admin_accounts.list_admins"))
    return render_template("modals/create-admin.html", form=form, access_mode=True)


# ── Edit ───────────────────────────────────────────────────────────────
@admin_accounts_bp.route("/<int:admin_id>/edit", methods=["GET", "POST"])
@login_required
def edit_admin(admin_id):
    acc = db.get_or_404(AdminAccount, admin_id)
    form = AdminUpdateForm(obj=acc)
    if form.validate_on_submit():
        # Username uniqueness check
        other = AdminAccount.query.filter_by(username=form.username.data).first()
        if other and other.id != acc.id:
            form.username.errors = [
                *list(form.username.errors),
                "Username already taken",
            ]
        elif _link_access_email(acc, form.access_email.data, form.access_email):
            acc.username = form.username.data
            acc.display_name = form.display_name.data or None
            if form.password.data:
                acc.set_password(form.password.data)
            db.session.commit()
            flash(_("Admin updated"), "success")
            return redirect(url_for("admin_accounts.list_admins"))
        else:
            db.session.rollback()
    if request.headers.get("HX-Request"):
        return render_template("modals/edit-admin.html", form=form, admin=acc)
    return render_template("modals/edit-admin.html", form=form, admin=acc)


# ── Delete ─────────────────────────────────────────────────────────────
@admin_accounts_bp.route("/", methods=["DELETE"])
@login_required
def delete_admin():
    """Delete an admin account and return updated list for HTMX requests."""
    admin_id = request.args.get("delete")

    if admin_id:
        # Use synchronize_session=False for performance; no loaded objects are in session
        AdminAccount.query.filter_by(id=int(admin_id)).delete(synchronize_session=False)
        db.session.commit()

    # If the request came from HTMX, send back the refreshed admins partial so the
    # client can swap it in seamlessly (keeping the UI in sync without a full page reload).
    if request.headers.get("HX-Request"):
        admins = AdminAccount.query.order_by(AdminAccount.username).all()
        return render_template("settings/admins.html", admins=admins)

    # Non-HTMX fall-back: redirect back to the list page
    return redirect(url_for("admin_accounts.list_admins"))


# Add a route for user profile that's not under the settings prefix


@admin_bp.route("/profile", methods=["GET"])
@login_required
def user_profile():
    """Render user profile page."""
    return render_template("profile.html")


@admin_bp.route("/profile/change-password", methods=["POST"])
@login_required
def change_password():
    """Change user password with HTMX response."""

    if not isinstance(current_user, AdminAccount):
        return render_template(
            "components/password_result.html",
            error="Only admin accounts can change passwords",
        )

    current_password = request.form.get("current_password")
    new_password = request.form.get("new_password")
    confirm_password = request.form.get("confirm_password")

    # Validation
    if not current_password or not new_password or not confirm_password:
        return render_template(
            "components/password_result.html", error="All password fields are required"
        )

    if new_password != confirm_password:
        return render_template(
            "components/password_result.html", error="New passwords do not match"
        )

    if not current_user.check_password(current_password):
        return render_template(
            "components/password_result.html", error="Current password is incorrect"
        )

    if len(new_password) < 6:
        return render_template(
            "components/password_result.html",
            error="New password must be at least 6 characters long",
        )

    try:
        current_user.set_password(new_password)
        db.session.commit()
        return render_template(
            "components/password_result.html", success="Password changed successfully"
        )
    except Exception:
        db.session.rollback()
        return render_template(
            "components/password_result.html", error="Failed to change password"
        )


@admin_accounts_bp.route("/<int:admin_id>/reset-passkeys", methods=["POST"])
@login_required
def reset_passkeys(admin_id):
    """Reset all passkeys for a specific admin account."""
    admin = db.get_or_404(AdminAccount, admin_id)

    try:
        # Delete all passkeys for this admin
        WebAuthnCredential.query.filter_by(admin_account_id=admin_id).delete()
        db.session.commit()
        flash(
            _("All passkeys for {} have been reset").format(admin.username), "success"
        )
    except Exception:
        db.session.rollback()
        flash(_("Failed to reset passkeys for {}").format(admin.username), "error")

    return redirect(url_for("admin_accounts.list_admins"))


@admin_accounts_bp.route("/<int:admin_id>/passkeys", methods=["GET"])
@login_required
def admin_passkeys(admin_id):
    """View passkeys for a specific admin account."""
    admin = db.get_or_404(AdminAccount, admin_id)
    passkeys = WebAuthnCredential.query.filter_by(admin_account_id=admin_id).all()

    if request.headers.get("HX-Request"):
        return render_template(
            "components/admin_passkeys.html", admin=admin, passkeys=passkeys
        )
    return render_template(
        "components/admin_passkeys.html", admin=admin, passkeys=passkeys
    )
