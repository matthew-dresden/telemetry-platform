#!/usr/bin/env bash
#
# terragrunt/common/scripts/empty-s3-bucket.sh
#
# Empty an S3 bucket (all object versions + delete markers) so that `terragrunt destroy`
# can delete the bucket even when it holds objects. Invoked as a terragrunt before_hook on
# the destroy command from the shared _envcommon templates (see _envcommon/data-lake.hcl),
# identically for every environment. This makes the whole env stack destroy/recreate-able
# from terragrunt alone with NO manual S3 empty step, independent of whether the bucket's
# module sets force_destroy -- so it works the same for local module sources (sandbox) and
# pinned immutable module sources (prod).
#
# The destroy runs with no ambient AWS credentials in the shell environment (the per-unit
# AWS account is selected by a named profile in the generated provider), so this script is
# given the unit's named profile explicitly and scopes every call to it.
#
# Idempotent: a bucket that does not exist (already destroyed, or never created) is a no-op
# success. Fail-fast on any real AWS error -- no silent failures, no fallback.
#
# Usage: empty-s3-bucket.sh <bucket-name> <aws-named-profile>
#
set -euo pipefail

BUCKET="${1:?empty-s3-bucket: bucket name required as arg 1}"
PROFILE="${2:?empty-s3-bucket: aws named profile required as arg 2}"
REGION="${AWS_DEFAULT_REGION:-us-east-1}"

# No-op when the bucket is absent (already torn down or never created).
if ! aws s3api head-bucket --bucket "$BUCKET" --profile "$PROFILE" --region "$REGION" >/dev/null 2>&1; then
  echo "empty-s3-bucket: bucket '${BUCKET}' does not exist -- nothing to empty"
  exit 0
fi

echo "empty-s3-bucket: emptying '${BUCKET}' (profile=${PROFILE}, region=${REGION})"

# Delete every object version and delete marker, in batches of up to 1000. The AWS calls
# are all aws-cli; python3 is used only as JSON glue to build each delete batch (no jq
# dependency). The loop terminates when a listing returns no versions or markers.
total=0
while : ; do
  listing="$(aws s3api list-object-versions \
    --bucket "$BUCKET" --profile "$PROFILE" --region "$REGION" --max-items 1000 \
    --output json)"

  batch="$(printf '%s' "$listing" | python3 -c '
import sys, json
data = json.load(sys.stdin)
objects = [
    {"Key": o["Key"], "VersionId": o["VersionId"]}
    for o in (data.get("Versions") or []) + (data.get("DeleteMarkers") or [])
]
print(json.dumps({"Objects": objects, "Quiet": True}) if objects else "")
')"

  [ -z "$batch" ] && break

  batch_count="$(printf '%s' "$batch" | python3 -c 'import sys, json; print(len(json.load(sys.stdin)["Objects"]))')"
  printf '%s' "$batch" | aws s3api delete-objects \
    --bucket "$BUCKET" --profile "$PROFILE" --region "$REGION" \
    --delete file:///dev/stdin >/dev/null
  total=$((total + batch_count))
  echo "empty-s3-bucket: deleted ${batch_count} (running total ${total})"
done

echo "empty-s3-bucket: '${BUCKET}' emptied (${total} versions/markers removed)"
