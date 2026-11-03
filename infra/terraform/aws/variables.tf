variable "name" {
  description = "Base name for all resources."
  type        = string
  default     = "nexus-node"
}

variable "environment" {
  description = "Environment name (prod, staging, ...)."
  type        = string
  default     = "prod"
}

variable "region" {
  description = "AWS region."
  type        = string
  default     = "il-central-1"
}

variable "vpc_cidr" {
  description = "CIDR block of the VPC."
  type        = string
  default     = "10.40.0.0/16"
}

variable "kubernetes_version" {
  description = "EKS control plane version."
  type        = string
  default     = "1.33"
}

variable "node_instance_types" {
  description = "Instance types for the managed node group (first match wins for Spot)."
  type        = list(string)
  default     = ["t3.medium", "t3a.medium"]
}

variable "node_capacity_type" {
  description = "ON_DEMAND or SPOT."
  type        = string
  default     = "SPOT"

  validation {
    condition     = contains(["ON_DEMAND", "SPOT"], var.node_capacity_type)
    error_message = "node_capacity_type must be ON_DEMAND or SPOT."
  }
}

variable "node_count" {
  description = "Desired/min/max size of the node group."
  type = object({
    min     = number
    desired = number
    max     = number
  })
  default = {
    min     = 1
    desired = 2
    max     = 3
  }
}

variable "cluster_endpoint_public_access_cidrs" {
  description = "CIDRs allowed to reach the public EKS API endpoint. Narrow this to your IP."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "github_repository" {
  description = "GitHub repo (owner/name) allowed to push images via OIDC."
  type        = string
  default     = "Sell6ix/Nexus-Node"
}

variable "github_branch" {
  description = "Branch whose workflows may assume the CI role."
  type        = string
  default     = "main"
}

variable "create_github_oidc_provider" {
  description = "Set false if the account already has the token.actions.githubusercontent.com provider."
  type        = bool
  default     = true
}

variable "snapshot_retention_days" {
  description = "How many daily EBS snapshots of the app data volume to keep."
  type        = number
  default     = 7
}
