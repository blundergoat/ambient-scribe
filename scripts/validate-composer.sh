#!/bin/bash
# Validate Composer metadata while allowing one reviewed dev-client pin.
#
# The UI talks to the Python agent through blundergoat/strands-php-client.
# This project is intentionally testing a commit-pinned dev branch of that
# client, so Composer's strict commit-ref warning is accepted only for that
# package. All other warnings and errors still fail the developer gate.

set -o pipefail

ALLOWED_STRANDS_CLIENT_WARNING='- The package "blundergoat/strands-php-client" is pointing to a commit-ref, this is bad practice and can cause unforeseen issues.'

validation_output="$(composer validate --strict 2>&1)"
validation_exit=$?

# A zero exit means Composer found no metadata warning before the user records a visit.
if [[ $validation_exit -eq 0 ]]; then
    printf '%s\n' "$validation_output"
    exit 0
fi

# Exit codes above warnings mean Composer found an actual schema or file problem.
if [[ $validation_exit -ne 1 ]]; then
    printf '%s\n' "$validation_output" >&2
    exit "$validation_exit"
fi

warning_count=$(printf '%s\n' "$validation_output" | grep -c '^- ' || true)

# The only accepted warning is the reviewed dev-client commit pin.
if [[ $warning_count -eq 1 ]] \
    && printf '%s\n' "$validation_output" | grep -Fq -- "$ALLOWED_STRANDS_CLIENT_WARNING"; then
    printf '%s\n' "$validation_output"
    printf '%s\n' "Allowed warning: requested strands-php-client dev commit pin."
    exit 0
fi

printf '%s\n' "$validation_output" >&2
exit "$validation_exit"
