"""Server-side validation for FruitVista forms.

All validation rules live in this module so that:

* the routes stay short and easy to read,
* every value coming from the browser is checked before it reaches the
  database,
* later phases (manual sales entry, reports) reuse the same helpers.

Every ``validate_*`` function returns a ``(cleaned_value, error_message)``
pair. ``cleaned_value`` is ``None`` when the input is rejected.
"""

import re

NAME_MIN = 3
NAME_MAX = 80
EMAIL_MAX = 120
PASSWORD_MIN = 8
PASSWORD_MAX = 128

NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z .'-]*$")
EMAIL_PATTERN = re.compile(
    r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$"
)
# Indian mobile numbers: ten digits beginning with 6, 7, 8 or 9.
MOBILE_PATTERN = re.compile(r"^[6-9]\d{9}$")


def clean_text(value):
    """Trim surrounding spaces from a submitted value."""
    return (value or "").strip()


def read_registration_form(source):
    """Copy non-sensitive values so the form can be re-rendered after an error.

    Passwords are deliberately not copied back into the page.
    """
    return {
        "full_name": clean_text(source.get("full_name")),
        "email": clean_text(source.get("email")),
        "mobile": clean_text(source.get("mobile")),
        "district": clean_text(source.get("district")),
    }


def validate_full_name(value):
    """Accept 3-80 characters containing letters, spaces, dots, hyphens, apostrophes."""
    name = clean_text(value)

    if not name:
        return None, "Please enter your full name."
    if len(name) < NAME_MIN:
        return None, f"Full name must be at least {NAME_MIN} characters long."
    if len(name) > NAME_MAX:
        return None, f"Full name must be {NAME_MAX} characters or fewer."
    if not NAME_PATTERN.match(name):
        return (
            None,
            "Full name may only contain letters, spaces, dots, hyphens and apostrophes.",
        )

    return re.sub(r"\s+", " ", name), None


def validate_email(value):
    """Accept a well-formed email address and return it in lower case."""
    email = clean_text(value).lower()

    if not email:
        return None, "Please enter your email address."
    if len(email) > EMAIL_MAX:
        return None, f"Email address must be {EMAIL_MAX} characters or fewer."
    if not EMAIL_PATTERN.match(email):
        return None, "Please enter a valid email address, for example name@example.com."

    return email, None


def validate_mobile(value):
    """Accept an Indian mobile number, with or without a country code.

    ``+91 98765 43210``, ``09876543210`` and ``9876543210`` are all stored as
    ``9876543210``.
    """
    digits = re.sub(r"\D", "", clean_text(value))

    if not digits:
        return None, "Please enter your mobile number."

    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]

    if not MOBILE_PATTERN.match(digits):
        return (
            None,
            "Enter a valid 10-digit mobile number starting with 6, 7, 8 or 9.",
        )

    return digits, None


def validate_district(value, districts):
    """Accept one of the supported districts, ignoring case and spacing.

    The value returned is always the project's own spelling, so a district
    typed into an environment variable as ``mysuru``, ``MYSURU`` or
    ``Bengaluru  Urban`` is accepted and stored as ``Mysuru`` or
    ``Bengaluru Urban``. A name that is not one of the supported districts is
    still refused, so no unsupported value can ever be stored.
    """
    district = clean_text(value)

    if not district:
        return None, "Please select your district."

    normalised = re.sub(r"\s+", " ", district).casefold()

    for supported in districts:
        if supported.casefold() == normalised:
            return supported, None

    return None, "Please choose one of the supported districts."


def validate_password(value):
    """Check the strength of a new password."""
    password = value or ""

    if not password:
        return None, "Please choose a password."
    if len(password) < PASSWORD_MIN:
        return None, f"Password must be at least {PASSWORD_MIN} characters long."
    if len(password) > PASSWORD_MAX:
        return None, f"Password must be {PASSWORD_MAX} characters or fewer."
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        return None, "Password must contain at least one letter and one number."

    return password, None


def validate_confirm_password(password, confirm_password):
    """Check that the confirmation field matches the password."""
    if not confirm_password:
        return "Please confirm your password."
    if password != confirm_password:
        return "Password and confirm password do not match."
    return None


def validate_registration_fields(source, districts):
    """Validate the account fields shared by registration and the profile form.

    Only ``full_name``, ``email``, ``mobile`` and ``district`` are read. A
    submitted ``role`` (or any other field) is ignored, so this helper can
    never be used to change privileges.

    Returns ``(cleaned, errors)``.
    """
    errors = {}

    full_name, error = validate_full_name(source.get("full_name"))
    if error:
        errors["full_name"] = error

    email, error = validate_email(source.get("email"))
    if error:
        errors["email"] = error

    mobile, error = validate_mobile(source.get("mobile"))
    if error:
        errors["mobile"] = error

    district, error = validate_district(source.get("district"), districts)
    if error:
        errors["district"] = error

    cleaned = {
        "full_name": full_name,
        "email": email,
        "mobile": mobile,
        "district": district,
    }

    return cleaned, errors


def validate_registration(source, districts):
    """Validate the whole registration form.

    ``source`` is the submitted form (``request.form``). Returns
    ``(cleaned, errors)`` where ``errors`` maps a field name to one readable
    message. ``errors`` is empty when every field is valid.
    """
    cleaned, errors = validate_registration_fields(source, districts)

    password, error = validate_password(source.get("password"))
    if error:
        errors["password"] = error

    confirm_error = validate_confirm_password(
        source.get("password") or "", source.get("confirm_password") or ""
    )
    if confirm_error:
        errors["confirm_password"] = confirm_error

    cleaned["password"] = password

    return cleaned, errors
