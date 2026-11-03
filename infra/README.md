# Nexus Node on AWS (EKS + Argo CD)

Running the hub on Amazon EKS as an alternative to a home PC.

```
GitHub push to main
   │  CI: pytest · web build · terraform validate · helm lint · docker smoke test
   ▼
Release workflow ── OIDC ──▶ IAM role ──▶ push image to ECR (tag = commit SHA)
   │
   └─ bumps deploy/helm/nexus-node/values-prod.yaml
                      │
                      ▼
               Argo CD (in EKS) syncs Helm chart ──▶ Deployment + PVC (gp3, encrypted)
                                                          │
                                       DLM daily EBS snapshots (7 kept)
```

## What Terraform creates

| File | Resources |
|------|-----------|
| `terraform/bootstrap` | S3 bucket for remote state (versioned, encrypted, private) |
| `terraform/aws/main.tf` | VPC (2 AZ, private nodes, single NAT), EKS with managed Spot node group, addons incl. EBS CSI + Pod Identity |
| `terraform/aws/iam.tf` | EBS CSI role (Pod Identity), GitHub OIDC provider + least-privilege ECR push role restricted to `main` |
| `terraform/aws/ecr.tf` | ECR repo: immutable tags, scan on push, lifecycle policy |
| `terraform/aws/backup.tf` | Data Lifecycle Manager policy: daily snapshots of volumes tagged `Backup=nexus-node` |

## Deploy

```bash
# 1. State bucket (once per account)
cd infra/terraform/bootstrap
terraform init && terraform apply -var state_bucket_name=nexus-node-tfstate-<account-id>

# 2. Cluster
cd ../aws
cp backend.hcl.example backend.hcl            # fill in the bucket name
cp terraform.tfvars.example terraform.tfvars  # set your IP for the API endpoint
terraform init -backend-config=backend.hcl
terraform plan -out tf.plan && terraform apply tf.plan
$(terraform output -raw kubeconfig_command)

# 3. GitHub repo variables (Settings → Variables → Actions)
#    AWS_ROLE_ARN   = terraform output github_ci_role_arn
#    AWS_REGION     = il-central-1
#    ECR_REPOSITORY = nexus-node

# 4. Argo CD + app secret
kubectl create namespace argocd
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
kubectl create namespace nexus-node
kubectl -n nexus-node create secret generic nexus-node-env \
  --from-literal=NEXUS_SECRETS_KEY="$(openssl rand -base64 32)" \
  --from-literal=NEXUS_JWT_SECRET="$(openssl rand -hex 32)"
kubectl apply -f deploy/argocd/application.yaml

# 5. Open the hub (not exposed publicly by default)
kubectl -n nexus-node port-forward svc/nexus-node 8743:80
```

`il-central-1` (Tel Aviv) is an opt-in region; enable it in the AWS account or set `region` to e.g. `eu-central-1`.

## Notes

- **Single replica, `Recreate` rollout**: the hub stores state as files in `/data`, so it can't scale horizontally on an RWO volume.
- **Hardened pod**: non-root UID 10001, read-only root FS, all capabilities dropped, no SA token mounted.
- **Cost**: EKS control plane (~$73/mo) + NAT gateway + 2 Spot `t3.medium`. `terraform destroy` when not in use; the PVC and StorageClass use `Retain`, so delete leftover EBS volumes manually.
- **Local checks**: `terraform fmt -check -recursive`, `terraform validate`, `helm lint deploy/helm/nexus-node`.
