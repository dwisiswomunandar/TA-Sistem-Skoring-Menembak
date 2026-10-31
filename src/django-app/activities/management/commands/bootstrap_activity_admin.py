"""Interactive deployment entry point; no password command-line argument exists."""

import sys
from getpass import getpass

from django.core.exceptions import ImproperlyConfigured, PermissionDenied, ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.views.decorators.debug import sensitive_variables

from activities.bootstrap import bootstrap_activity_administrator, deployment_plans


class Command(BaseCommand):
    help = "Bootstrap one explicitly approved initial activity administrator."
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("--actor-email", required=True)
        parser.add_argument("--activity-id", type=int, required=True)
        parser.add_argument("--recipient-id", type=int, required=True)
        parser.add_argument("--permission", nargs="+", required=True, dest="permissions")
        parser.add_argument("--operation-id", required=True)
        parser.add_argument("--reason", required=True)
        parser.add_argument("--approval-reference", required=True)

    @sensitive_variables("password")
    def handle(self, *args, **options):
        try:
            deployment_plans()
            if not sys.stdin.isatty():
                raise CommandError("An interactive deployment terminal is required.")
            password = getpass("Deployment actor password: ")
            receipt = bootstrap_activity_administrator(actor_email=options["actor_email"], actor_password=password,
                activity_id=options["activity_id"], recipient_id=options["recipient_id"],
                permissions=options["permissions"], operation_id=options["operation_id"],
                reason=options["reason"], approval_reference=options["approval_reference"])
        except (ImproperlyConfigured, PermissionDenied, ValidationError):
            raise CommandError("Bootstrap rejected. Check deployment approval, command plan and actor authentication.") from None
        self.stdout.write(f"Bootstrap recorded: activity={receipt.activity_id} recipient={receipt.recipient_id} "
                          f"audit={receipt.audit_event_id} operation={receipt.operation_id}")
