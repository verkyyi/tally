#!/bin/bash
# Where readers came from over the last days, as JSON (growth.snapshot): Search Console queries and pages, referring sites,
# exact landing pages, assistant fetches, crawlers, unanswered searches. Read-only.
#   AWS_PROFILE=admin bash infra/traffic.sh [DAYS]
set -euo pipefail
export AWS_PAGER=""
REGION=us-east-1
DAYS=${1:-7}
case "$DAYS" in ''|*[!0-9]*) echo "DAYS must be a number" >&2; exit 2;; esac
IID=$(aws ec2 describe-instances --region $REGION --filters Name=tag:Project,Values=tally Name=instance-state-name,Values=running --query 'Reservations[0].Instances[0].InstanceId' --output text)
CMD="cd /opt/tally/app && sudo -u tally TALLY_AUTOSYNC=0 .venv/bin/python setup_demo.py snapshot $DAYS"
CID=$(aws ssm send-command --region $REGION --instance-ids "$IID" --document-name AWS-RunShellScript --parameters "commands=[\"$CMD\"]" --query Command.CommandId --output text)
for i in $(seq 1 30); do
  S=$(aws ssm get-command-invocation --region $REGION --command-id "$CID" --instance-id "$IID" --query Status --output text 2>/dev/null || echo Pending)
  [ "$S" = "Success" ] || [ "$S" = "Failed" ] && break; sleep 2
done
aws ssm get-command-invocation --region $REGION --command-id "$CID" --instance-id "$IID" --query StandardOutputContent --output text
