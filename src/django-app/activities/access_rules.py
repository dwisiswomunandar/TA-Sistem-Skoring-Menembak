"""Closed registry of supported draft operations; role names confer no authority."""

DRAFT_PERMISSIONS = {
    "assessment.draft_created": ("assessments.add_assessment",),
    "assessment.draft_updated": ("assessments.change_assessment",),
    "shotpoint.added": ("assessments.change_assessment", "assessments.add_shotpoint"),
    "shotpoint.updated": ("assessments.change_assessment", "assessments.change_shotpoint"),
    "shotpoint.deleted": ("assessments.change_assessment", "assessments.delete_shotpoint"),
}
GRANT_PERMISSIONS = frozenset(p for values in DRAFT_PERMISSIONS.values() for p in values)
