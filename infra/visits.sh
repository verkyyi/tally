#!/bin/bash
# Who is coming to the live site: page views by referring site, crawler visits, top pages, accounts. Read-only.
#   bash infra/visits.sh
set -euo pipefail
export AWS_PAGER=""
REGION=us-east-1
IID=$(aws ec2 describe-instances --region $REGION --filters Name=tag:Project,Values=tally Name=instance-state-name,Values=running --query 'Reservations[0].Instances[0].InstanceId' --output text)
Q=$(printf '%s' 'import sqlite3
c=sqlite3.connect("file:/opt/tally/data/tally.db?mode=ro",uri=True)
rows=c.execute("select ref, sum(n) from visits where path!=\"*visitors\" group by ref order by 2 desc").fetchall()
print("distinct visitors by day and referring site:", c.execute("select day, coalesce(nullif(ref,\"\"),\"direct\"), n from visits where path=\"*visitors\" order by day desc, n desc limit 12").fetchall())
print("page views by people, by referring site:", [(r or "direct", n) for r,n in rows if not (r or "").startswith("bot:")])
print("crawler visits:", [(r[4:], n) for r,n in rows if (r or "").startswith("bot:")])
print("by day:", c.execute("select day, sum(n) from visits where ref not like \"bot:%\" and path!=\"*visitors\" group by day order by day desc limit 7").fetchall())
print("top pages:", c.execute("select path, sum(n) from visits where ref not like \"bot:%\" and path!=\"*visitors\" group by path order by 2 desc limit 8").fetchall())
print("searched for, nothing found:", c.execute("select q, via, sum(n) from asks where found=0 group by q, via order by 3 desc limit 15").fetchall())
print("searched for, found:", c.execute("select q, via, sum(n) from asks where found>0 group by q, via order by 3 desc limit 10").fetchall())
print("data read by scripts and agents:", c.execute("select kind, sum(n) from fetches group by kind order by 2 desc").fetchall())
print("accounts:", c.execute("select count(*) from users").fetchone()[0], "| newest:", c.execute("select max(created_at) from users").fetchone()[0])' | base64 | tr -d '\n')
CID=$(aws ssm send-command --region $REGION --instance-ids "$IID" --document-name AWS-RunShellScript --parameters "commands=[\"echo $Q | base64 -d | sudo -u tally /opt/tally/app/.venv/bin/python -\"]" --query Command.CommandId --output text)
for i in $(seq 1 20); do
  S=$(aws ssm get-command-invocation --region $REGION --command-id "$CID" --instance-id "$IID" --query Status --output text 2>/dev/null || echo Pending)
  [ "$S" = "Success" ] || [ "$S" = "Failed" ] && break; sleep 2
done
aws ssm get-command-invocation --region $REGION --command-id "$CID" --instance-id "$IID" --query StandardOutputContent --output text
