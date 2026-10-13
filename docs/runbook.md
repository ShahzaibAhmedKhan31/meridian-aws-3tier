# Meridian — Teardown & Rebuild Runbook

Operational runbook for **Meridian**, a highly available three-tier platform on AWS (`us-east-1`): Nginx web tier and Node.js/PM2 app tier on EC2 Auto Scaling, behind external and internal ALBs, with RDS MySQL Multi-AZ.

**Purpose:** keep a learning project near-zero cost by deleting what bills hourly between sessions, and rebuild it in a known order without rediscovering the same failures.

> **Cost model:** NAT Gateway, both ALBs, running EC2 instances and a running RDS instance bill by the hour. Everything else (VPC, subnets, route tables, security groups, IAM, launch templates, target groups, ASG definitions, SSM parameters, S3 bucket, CloudWatch dashboard and alarms) is free or near-free at rest and is kept. Approximate hourly-billed costs: NAT Gateway ~$32-40/mo, each ALB ~$16-20/mo, RDS Multi-AZ `db.t4g.micro` ~$25-30/mo.

## Resource inventory

| Layer | Resources |
|---|---|
| Network | `3tier-vpc` (10.0.0.0/16), 6 subnets across 2 AZs (public-web, private-app, private-db), Internet Gateway, route tables `public-rt` / `private-app-rt` / `private-db-rt`, NAT Gateway `nat-gateway-1a` |
| Load balancing | `web-tier-alb-external`, `app-tier-alb-internal`, target groups `web-tier-tg` (:80) and `app-tier-tg-v2` (:3000) |
| Compute | Launch templates `web-tier-lt`, `app-tier-lt`; ASGs `web-tier-asg`, `app-tier-asg` (min 1 / max 2, target-tracking CPU 60%) |
| Data | RDS MySQL `three-tier-db` (Multi-AZ), `db-subnet-group` |
| Config & security | SSM parameters `/3tier-app/db/{username,password,name,host}`, roles `EC2-Web-Tier-Role` and `EC2-App-Tier-Role`, security groups `sg-alb-external`, `sg-web`, `sg-alb-internal`, `sg-app`, `sg-db` |
| Observability | S3 bucket for ALB access logs (30-day lifecycle), CloudWatch dashboard `3tier-portfolio-dashboard` |

## Shutdown (end of session)

Work in this order so nothing is "in use" when you delete it.

1. **Scale both ASGs to 0.** Set desired and minimum to 0 on `web-tier-asg` and `app-tier-asg`. Wait until no instances are running.
2. **Delete both ALBs.** `web-tier-alb-external` and `app-tier-alb-internal`. ALBs have no stop state.
3. **Delete the NAT Gateway.** Then **release its Elastic IP** (it is not released automatically and bills ~$0.005/hr while unattached).
4. **Stop `three-tier-db`.** Stop it; do not delete it. AWS automatically restarts a stopped RDS instance after 7 days, so re-stop it if you are away longer than that.
5. **Leave everything else in place.**

**Confirm at $0/hr:** EC2 → Instances (none running), Load Balancers (none), VPC → NAT Gateways (none active), Elastic IPs (none unattached), RDS (`Stopped`). Residual cost is RDS storage only (~$2-3/mo for 20 GB).

## Rebuild (start of session)

Order matters: instances need the NAT Gateway to install packages, and the app tier needs the database to be available at boot.

1. **Start `three-tier-db`** and wait for status **Available**. The app reads its DB credentials once at startup; if the database is unavailable when an app instance boots, `/api/db-check` fails until that instance is restarted.
2. **Recreate the NAT Gateway** in `public-web-1a` with a new Elastic IP, and wait for **Available**.
3. **Update `private-app-rt`:** point `0.0.0.0/0` at the new NAT Gateway. The old route targets a deleted gateway and shows as a blackhole.
4. **Recreate `app-tier-alb-internal`:** scheme internal, both private-app subnets, `sg-alb-internal`, listener HTTP:80 forwarding to `app-tier-tg-v2`.
5. **Update the web tier's upstream address.** The internal ALB gets a new DNS name each time it is recreated, and `web-tier-lt` hardcodes it in the Nginx `proxy_pass` line. Create a new launch template version with the new DNS name (no trailing slash after it), point `web-tier-asg` at it, and refresh instances. If you want to skip this step permanently, leave the internal ALB running between sessions (~$16-20/mo) or put a stable internal DNS name in front of it.
6. **Recreate `web-tier-alb-external`:** internet-facing, both public subnets, `sg-alb-external`, listener HTTP:80 forwarding to `web-tier-tg`. Re-enable access logging to the S3 bucket under the `web-tier-alb/` prefix.
7. **Check ASG target group attachments.** In each ASG's *Load balancing* tab, confirm `web-tier-tg` and `app-tier-tg-v2` are still attached.
8. **Scale `app-tier-asg` to 1, then `web-tier-asg` to 1** (desired and minimum). Allow the health check grace periods (app 240 s, web 300 s) before judging health.

## Verification

Replace `<external-alb-dns>` with the new external ALB DNS name. Use `curl`, not a browser: browsers may silently upgrade `http://` to `https://`, and there is no HTTPS listener.

```bash
curl -s http://<external-alb-dns>/health          # web tier: expect OK
curl -s http://<external-alb-dns>/api/status      # full chain to app tier: expect JSON with instanceId
curl -s http://<external-alb-dns>/api/db-check    # full chain to RDS: expect {"dbConnected":true,"result":1,...}
```

All three passing means: internet → external ALB → Nginx → internal ALB → Node.js → RDS is healthy end to end. Also confirm both target groups show **healthy** targets.

## Failure reference

Failures hit during the build, and what they looked like.

| Symptom | Likely cause | Fix |
|---|---|---|
| ASG keeps replacing instances that never become healthy | Health check grace period shorter than boot time | Raise the grace period (web 300 s, app 240 s) |
| Target group shows zero targets while the ASG says instance is healthy | ASG not attached to the target group | ASG → Load balancing tab → attach the target group |
| Target "unhealthy: request timed out" with correct security groups | Target group registered on the wrong port (cannot be edited) | Create a new target group on the right port and re-point the listener and ASG |
| `/api/status` returns 404 through Nginx but a health route works | Trailing slash on `proxy_pass` strips the `/api/` prefix | Remove the trailing slash |
| `/api/*` returns 502/504 after rebuilding the internal ALB | Nginx still points at the old internal ALB DNS name | Rebuild step 5 |
| `/api/db-check` fails but `/health` passes | App instance booted before the database was available | Start RDS first; refresh or restart the app instance |
| ASG will not scale out although CPU looks high | Single Node process caps total CPU near 50% on 2 vCPUs, below the 60% threshold | PM2 cluster mode (`-i max`) |
| Browser shows connection timeout, `curl` returns a normal response | Browser upgraded to HTTPS | Test with `curl` |

## Full decommission (end of project)

Delete in this order: both ASGs → both ALBs → both target groups (including any leftover original `app-tier-tg`) → both launch templates → RDS instance (skip the final snapshot unless you want one) → DB subnet group → NAT Gateway and its Elastic IP → custom route tables → Internet Gateway (detach, then delete) → subnets → VPC → security groups → S3 bucket (empty it first) → SSM parameters → IAM roles → CloudWatch dashboard. Then check that no CloudWatch alarms were orphaned.
