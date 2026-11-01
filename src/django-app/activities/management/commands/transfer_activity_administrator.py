"""Interactive deployment command; passwords never enter arguments or approval plans."""

import sys
from getpass import getpass

from django.core.exceptions import ImproperlyConfigured, PermissionDenied, ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.views.decorators.debug import sensitive_variables

from activities.administrator_transfer import deployment_plans, transfer_activity_administrator


class Command(BaseCommand):
    help = "Execute one explicitly approved normal activity administrator handover."
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("--operation-id", required=True)
        parser.add_argument("--source-email", required=True)
        parser.add_argument("--successor-email", required=True)

    @sensitive_variables("source_password", "successor_password")
    def handle(self, *args, **options):
        try:
            deployment_plans()
            if not sys.stdin.isatty():
                raise CommandError("An interactive deployment terminal is required.")
            source_password = getpass("Source administrator password: ")
            successor_password = getpass("Successor confirmation password: ")
            receipt = transfer_activity_administrator(source_email=options["source_email"],
                source_password=source_password, successor_email=options["successor_email"],
                successor_password=successor_password, operation_id=options["operation_id"])
        except (ImproperlyConfigured, PermissionDenied, ValidationError):
            raise CommandError("Transfer rejected. Check deployment approval, current state and authentication.") from None
        self.stdout.write(f"Transfer recorded: activity={receipt.activity_id} source={receipt.source_id} "
                          f"recipient={receipt.recipient_id} audit={receipt.audit_event_id} operation={receipt.operation_id}")
