"""Closed registry of supported draft operations; role names confer no authority."""

DRAFT_PERMISSIONS = {
    "assessment.draft_created": ("assessments.add_assessment",),
    "assessment.draft_updated": ("assessments.change_assessment",),
    "shotpoint.added": ("assessments.change_assessment", "assessments.add_shotpoint"),
    "shotpoint.updated": ("assessments.change_assessment", "assessments.change_shotpoint"),
    "shotpoint.deleted": ("assessments.change_assessment", "assessments.delete_shotpoint"),
}
DELEGABLE_PERMISSIONS = frozenset(p for values in DRAFT_PERMISSIONS.values() for p in values)
GRANT_COMMAND_PERMISSIONS = {
    "activity_grant.issued": "activities.issue_activitypermissiongrant",
    "activity_grant.revoked": "activities.revoke_activitypermissiongrant",
    "activity_grant.reactivated": "activities.reactivate_activitypermissiongrant",
}
ADMINISTRATIVE_PERMISSIONS = frozenset(GRANT_COMMAND_PERMISSIONS.values())
GRANT_PERMISSIONS = DELEGABLE_PERMISSIONS | ADMINISTRATIVE_PERMISSIONS


def permission_key(permission):
    """Registry key includes the content-type model, not just a codename."""
    return (permission.content_type.app_label, permission.content_type.model, permission.codename)


def registered_permission_key(name):
    app, code = name.split(".")
    model = "activitypermissiongrant" if name in ADMINISTRATIVE_PERMISSIONS else code.split("_", 1)[1]
    return app, model, code


REGISTERED_GRANT_KEYS = frozenset(registered_permission_key(name) for name in GRANT_PERMISSIONS)
