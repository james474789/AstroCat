"""
Password Policy Utilities
Provides password validation rules.
"""

import re
from typing import List


COMMON_PASSWORDS = {
    "password",
    "password123",
    "admin",
    "admin123",
    "letmein",
    "qwerty",
    "welcome",
    "changeme",
}


def validate_password(password: str) -> List[str]:
    """
    Validate password strength and return a list of errors.
    """
    errors: List[str] = []

    if not password:
        return ["Password is required"]

    if len(password) < 12:
        errors.append("Password must be at least 12 characters long")

    if not re.search(r"[a-z]", password):
        errors.append("Password must include a lowercase letter")

    if not re.search(r"[A-Z]", password):
        errors.append("Password must include an uppercase letter")

    if not re.search(r"\d", password):
        errors.append("Password must include a number")

    if not re.search(r"[^A-Za-z0-9]", password):
        errors.append("Password must include a symbol")

    lowered = password.lower()
    if lowered in COMMON_PASSWORDS:
        errors.append("Password is too common")

    return errors