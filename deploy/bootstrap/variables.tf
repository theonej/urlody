variable "project_id" {
  description = "Project the API is deployed into (the same one deploy/ targets)."
  type        = string
}

variable "region" {
  description = "Region used by deploy/; the Cloud Build staging bucket is created here."
  type        = string
  default     = "us-central1"
}

variable "github_repository" {
  description = "GitHub repository allowed to deploy, as owner/name."
  type        = string
  default     = "theonej/urlody"
}

variable "branch" {
  description = "Only workflows running on this branch may deploy."
  type        = string
  default     = "develop"
}

variable "state_bucket" {
  description = "Bucket holding the Terraform state of deploy/; the deployer gets read/write access to it."
  type        = string
  default     = "urlody-tfstate-dev"
}
