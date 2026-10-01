locals {
  pool_id = "github"
  # Everything deploy/ touches. Scoped to this project; the state bucket is handled separately.
  deployer_roles = [
    "roles/viewer",                          # read the project (data.google_project)
    "roles/serviceusage.serviceUsageAdmin",  # enable the APIs
    "roles/artifactregistry.admin",          # the image repository and its IAM
    "roles/cloudbuild.builds.editor",        # build the image
    "roles/secretmanager.admin",             # the two secrets and their IAM
    "roles/iam.serviceAccountAdmin",         # the Cloud Run runtime service account
    "roles/iam.serviceAccountUser",          # deploy a service that runs as it
    "roles/run.admin",                       # the Cloud Run service and its public access
    "roles/resourcemanager.projectIamAdmin", # grant the build accounts their roles
  ]
}

resource "google_project_service" "apis" {
  for_each = toset([
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "sts.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "cloudbuild.googleapis.com",
    "storage.googleapis.com",
  ])
  service            = each.key
  disable_on_destroy = false
}

data "google_project" "this" {
  depends_on = [google_project_service.apis]
}

# --- who GitHub becomes -------------------------------------------------------

resource "google_service_account" "deployer" {
  account_id   = "scorer-deployer"
  display_name = "Deploys the scorer API from GitHub Actions"
  depends_on   = [google_project_service.apis]
}

resource "google_project_iam_member" "deployer" {
  for_each = toset(local.deployer_roles)
  project  = var.project_id
  role     = each.key
  member   = "serviceAccount:${google_service_account.deployer.email}"
}

# Terraform state of deploy/ lives here.
resource "google_storage_bucket_iam_member" "deployer_state" {
  bucket = var.state_bucket
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.deployer.email}"
}

# `gcloud builds submit` stages the source in this bucket, creating it on first
# use; making it here means the deployer only needs rights on the bucket.
resource "google_storage_bucket" "cloudbuild_staging" {
  name                        = "${var.project_id}_cloudbuild"
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = true
  lifecycle_rule {
    condition {
      age = 7
    }
    action {
      type = "Delete"
    }
  }
  depends_on = [google_project_service.apis]
}

resource "google_storage_bucket_iam_member" "deployer_staging" {
  bucket = google_storage_bucket.cloudbuild_staging.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.deployer.email}"
}

# --- how GitHub proves who it is ---------------------------------------------

resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = local.pool_id
  display_name              = "GitHub Actions"
  depends_on                = [google_project_service.apis]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github"
  display_name                       = "GitHub Actions OIDC"

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
    "attribute.ref"        = "assertion.ref"
  }

  # Tokens from any other repository are refused outright.
  attribute_condition = "assertion.repository == \"${var.github_repository}\""
}

# ... and only workflows running on the deploy branch may act as the deployer.
resource "google_service_account_iam_member" "github_impersonates_deployer" {
  service_account_id = google_service_account.deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.ref/refs/heads/${var.branch}"
}
