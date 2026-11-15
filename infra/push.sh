#!/bin/bash
# From the laptop: publish config (and optionally the database), then redeploy the instance through SSM.
#   infra/push.sh            push .env + redeploy code
#   infra/push.sh --db       also replace the server database with the local one
set -euo pipefail
umask 077
cd "$(dirname "$0")/.."
export AWS_PAGER=""
REGION=us-east-1
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
IID=$(aws ec2 describe-instances --region $REGION --filters Name=tag:Project,Values=tally Name=instance-state-name,Values=running --query 'Reservations[0].Instances[0].InstanceId' --output text)
{ grep -v '^TALLY_DB=' .env; cat infra/server.env; } > /tmp/tally.env.$$
aws ssm put-parameter --region $REGION --name /tally/env --type SecureString --overwrite --value "file:///tmp/tally.env.$$" --query Version --output text
rm -f /tmp/tally.env.$$
sleep 6  # the parameter store can serve the old value for a few seconds; the server must not fetch it before the new one is readable
ARG=""
if [ "${1:-}" = "--db" ]; then
  .venv/bin/python -c "import sqlite3;c=sqlite3.connect('tally.db');c.execute('PRAGMA wal_checkpoint(TRUNCATE)');c.close()"
  aws s3 cp --quiet tally.db "s3://tally-data-$ACCOUNT/tally.db"; ARG="--restore-db"
fi
CID=$(aws ssm send-command --region $REGION --instance-ids "$IID" --document-name AWS-RunShellScript \
  --parameters "commands=[\"bash /opt/tally/app/infra/remote_deploy.sh $ARG 2>&1 | tail -5\"]" --query Command.CommandId --output text)
for i in $(seq 1 40); do
  S=$(aws ssm get-command-invocation --region $REGION --command-id "$CID" --instance-id "$IID" --query Status --output text 2>/dev/null || echo Pending)
  [ "$S" = "Success" ] || [ "$S" = "Failed" ] && break; sleep 3
done
aws ssm get-command-invocation --region $REGION --command-id "$CID" --instance-id "$IID" --query '[Status,StandardOutputContent,StandardErrorContent]' --output text
