"""Every sentence a user can see, kept in one place.

These are written for a tired employee at the end of a shift: say what
happened and what to do next, in plain words, with no internal detail.
"""

SESSION_INVALID = "Your session is not valid or has expired. Please sign in again."
NOT_ALLOWED = "You don't have permission to do that."
BAD_REQUEST = "Something in that request wasn't right. Please check it and try again."
UNEXPECTED = "Something went wrong on our side. Please try again in a minute."

POLICY_UNREADABLE = "The policy document could not be read: {reason}. Nothing was changed."
POLICY_VERSION_CONFLICT = "{reason}. Upload a document with a higher version number. Nothing was changed."
POLICY_UPDATE_FAILED = (
    "The policy update did not complete, so the previous version is still the one in use. "
    "Please try the upload again in a few minutes."
)
