"""Every sentence a user can see, kept in one place.

These are written for a tired employee at the end of a shift: say what
happened and what to do next, in plain words, with no internal detail.
"""

SESSION_INVALID = "Your session is not valid or has expired. Please sign in again."
SIGN_IN_UNAVAILABLE = "Sign-in can't be checked right now. Please try again in a minute."
UNKNOWN_COMPANY = "We don't recognise that company. Please choose your company from the list and try again."
UNKNOWN_EMPLOYEE = (
    "No employee with that ID works at {company}. Check your employee ID and try again, or contact your transport desk."
)
ACCOUNT_INACTIVE = (
    "Your {company} employee account is no longer active, so you can't sign in. "
    "If you think this is a mistake, contact your HR or transport desk."
)
SIGNED_OUT = "You have been signed out."
TOO_MANY_ATTEMPTS = "Too many unsuccessful sign-in attempts. Please wait {wait} and try again."
NOT_ALLOWED = "You don't have permission to do that."
BAD_REQUEST = "Something in that request wasn't right. Please check it and try again."
UNEXPECTED = "Something went wrong on our side. Please try again in a minute."

POLICY_UNREADABLE = "The policy document could not be read: {reason}. Nothing was changed."
POLICY_VERSION_CONFLICT = "{reason}. Upload a document with a higher version number. Nothing was changed."
POLICY_NOT_YET_EFFECTIVE = (
    "This document takes effect on {date}. Please upload it on or after that date; until then {current} "
    "stays in use. Nothing was changed."
)
POLICY_UPDATE_FAILED = (
    "The policy update did not complete, so the previous version is still the one in use. "
    "Please try the upload again in a few minutes."
)

NOT_COVERED = (
    "Your company's transport policy doesn't cover this, so I can't give you a reliable answer. "
    "Please contact the transport desk."
)
NO_POLICY = (
    "Your company's transport policy hasn't been loaded yet, so I can't answer questions about it. "
    "Please contact the transport desk."
)
TRY_AGAIN = (
    "The assistant is taking too long to respond right now. Please try again in a minute. "
    "If it keeps happening, contact the transport desk."
)
ASSISTANT_UNAVAILABLE = (
    "The assistant isn't available at the moment. Please try again later, or contact the transport desk."
)
UNANSWERABLE = (
    "I couldn't put together a reliable answer from your policy. Please try rephrasing the question, "
    "or contact the transport desk."
)
OTHER_COMPANY = (
    "I can only answer from the {company} transport policy, which is the one for your account. "
    "I can't share or compare other companies' policies. If you meant your own policy, please ask "
    "again without the other company's name."
)
