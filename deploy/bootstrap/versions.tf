# One-time setup that lets GitHub Actions deploy: a deployer service account,
# and a Workload Identity Federation pool that trusts this repository's
# develop branch to act as it. Apply by hand, as a project owner, then put the
# two outputs into the repository's GitHub variables (see the README).
terraform {
  required_version = ">= 1.5"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }

  backend "gcs" {
    bucket = "urlody-tfstate-dev"
    prefix = "scorer-bootstrap"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}
