terraform {
  required_version = ">= 1.5"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # The state holds the Mailgun key and the API key in plain text, so it lives
  # in a private bucket rather than on disk.
  backend "gcs" {
    bucket = "urlody-tfstate-dev"
    prefix = "scorer"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}
