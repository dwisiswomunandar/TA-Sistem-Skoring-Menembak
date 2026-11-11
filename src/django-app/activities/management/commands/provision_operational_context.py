"""Interactive, bounded-file deployment adapter; no HTTP/admin or password flags."""

import json
import sys
from getpass import getpass

from django.core.exceptions import ImproperlyConfigured, PermissionDenied, ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.views.decorators.debug import sensitive_variables

from activities.context_provisioning import canonical_request, deployment_plans, provision_operational_context

MAX_REQUEST_BYTES = 65536


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field.")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError("Nonfinite JSON values are forbidden.")


def read_request(path):
    with open(path, "rb") as request_file:
        raw = request_file.read(MAX_REQUEST_BYTES + 1)
    if len(raw) > MAX_REQUEST_BYTES:
        raise ValueError("Request is too large.")
    data = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    return canonical_request(data)


class Command(BaseCommand):
    help = "Provision one explicitly approved NEW draft/pending/pending training context."
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("--actor-email", required=True)
        parser.add_argument("--request-file", required=True)

    @sensitive_variables("password")
    def handle(self, *args, **options):
        try:
            deployment_plans()
            if not sys.stdin.isatty():
                raise CommandError("An interactive deployment terminal is required.")
            request = read_request(options["request_file"])
            password = getpass("Provisioning actor password: ")
            receipt = provision_operational_context(
                actor_email=options["actor_email"], actor_password=password, data=request,
            )
        except (ImproperlyConfigured, PermissionDenied, ValidationError, OSError,
                ValueError, TypeError, RecursionError, EOFError):
            raise CommandError("Context provisioning rejected. Check the protected plan, request and authentication.") from None
        self.stdout.write(json.dumps(receipt.as_dict(), sort_keys=True))
