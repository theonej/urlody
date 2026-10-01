# The scorer API on Cloud Run.
#
# Cloud Run runs the same container as `docker run`, with the CPU kept
# allocated between requests (cpu_idle = false) so that the transcription,
# which runs after the 202 response, keeps running. The API key lives in
# Secret Manager and reaches the container as an environment variable. The
# Mailgun key never passes through Terraform at all: this creates an empty
# secret for it, its value is added by hand (see the mailgun_secret output),
# and the service reads it from Secret Manager at run time.

locals {
  root = "${path.module}/.."

  # Anything that changes the image: a new hash means a new build and a new revision.
  source_files = sort(concat(
    tolist(fileset(local.root, "src/**")),
    ["pyproject.toml", "uv.lock", "README.md", "Dockerfile", ".dockerignore"],
  ))
  source_hash = sha1(join("", [for f in local.source_files : filesha1("${local.root}/${f}")]))

  repository  = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
  built_image = "${local.repository}/${var.service_name}:${substr(local.source_hash, 0, 12)}"
  image       = var.build_image ? local.built_image : var.image

  mailgun_from = var.mailgun_from != "" ? var.mailgun_from : "scorer@${var.mailgun_domain}"
  api_key      = var.api_key != "" ? var.api_key : random_password.api_key.result

  # Cloud Build runs as the project's Cloud Build service account, or on newer
  # projects as the Compute Engine default account. Keyed by name, because the
  # project number inside the values is only known once the APIs are enabled.
  build_accounts = var.build_image ? {
    cloudbuild = "serviceAccount:${data.google_project.this.number}@cloudbuild.gserviceaccount.com"
    compute    = "serviceAccount:${data.google_project.this.number}-compute@developer.gserviceaccount.com"
  } : {}
}

resource "google_project_service" "apis" {
  for_each = toset([
    "run.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "secretmanager.googleapis.com",
  ])
  service            = each.key
  disable_on_destroy = false
}

data "google_project" "this" {
  depends_on = [google_project_service.apis]
}

# --- image -------------------------------------------------------------------

resource "google_artifact_registry_repository" "images" {
  repository_id = var.service_name
  location      = var.region
  format        = "DOCKER"
  description   = "Images for the ${var.service_name} API"
  depends_on    = [google_project_service.apis]
}

# Whichever account runs the build must be able to push the image here ...
resource "google_artifact_registry_repository_iam_member" "builders" {
  for_each   = local.build_accounts
  repository = google_artifact_registry_repository.images.id
  location   = var.region
  role       = "roles/artifactregistry.writer"
  member     = each.value
}

# ... and to write the build log.
resource "google_project_iam_member" "builders_log" {
  for_each = local.build_accounts
  project  = var.project_id
  role     = "roles/logging.logWriter"
  member   = each.value
}

resource "terraform_data" "image" {
  count = var.build_image ? 1 : 0

  triggers_replace = [local.built_image]

  provisioner "local-exec" {
    working_dir = local.root
    command     = "gcloud builds submit --project ${var.project_id} --tag ${local.built_image} ."
  }

  depends_on = [
    google_artifact_registry_repository_iam_member.builders,
    google_project_iam_member.builders_log,
  ]
}

# --- secrets -----------------------------------------------------------------

resource "random_password" "api_key" {
  length  = 40
  special = false
}

resource "google_secret_manager_secret" "secrets" {
  for_each  = toset(["mailgun-api-key", "api-key"])
  secret_id = "${var.service_name}-${each.key}"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]

  lifecycle {
    # Versions of the Mailgun secret are added outside Terraform; never drop them.
    prevent_destroy = true
  }
}

# Only the API key gets its value from here; the Mailgun key is added out of band.
resource "google_secret_manager_secret_version" "api_key" {
  secret      = google_secret_manager_secret.secrets["api-key"].id
  secret_data = local.api_key
}

# Earlier versions of this configuration wrote both secrets' values. Keep the
# API key's version under its new name, and let go of the Mailgun key's version
# without destroying it: the service is reading it.
moved {
  from = google_secret_manager_secret_version.secrets["api-key"]
  to   = google_secret_manager_secret_version.api_key
}

removed {
  from = google_secret_manager_secret_version.secrets

  lifecycle {
    destroy = false
  }
}

resource "google_service_account" "run" {
  account_id   = var.service_name
  display_name = "${var.service_name} Cloud Run service"
}

resource "google_secret_manager_secret_iam_member" "run_reads_secrets" {
  for_each  = google_secret_manager_secret.secrets
  secret_id = each.value.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.run.email}"
}

# --- service -----------------------------------------------------------------

resource "google_cloud_run_v2_service" "api" {
  name                = var.service_name
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false

  template {
    service_account                  = google_service_account.run.email
    timeout                          = "300s"
    max_instance_request_concurrency = 20

    scaling {
      min_instance_count = var.min_instances
      max_instance_count = var.max_instances
    }

    containers {
      image = local.image

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = var.cpu
          memory = var.memory
        }
        cpu_idle          = false # keep the CPU after the response: that is when the work happens
        startup_cpu_boost = true
      }

      env {
        name  = "MAILGUN_DOMAIN"
        value = var.mailgun_domain
      }
      env {
        name  = "MAILGUN_FROM"
        value = local.mailgun_from
      }
      env {
        name  = "MAILGUN_API_BASE"
        value = var.mailgun_api_base
      }
      env {
        name  = "SCORER_API_WORKERS"
        value = tostring(var.workers)
      }
      env {
        # The service reads the key itself, so a rotated key needs no redeploy.
        name  = "MAILGUN_API_KEY_SECRET"
        value = google_secret_manager_secret.secrets["mailgun-api-key"].id
      }
      env {
        name = "SCORER_API_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.secrets["api-key"].secret_id
            version = "latest"
          }
        }
      }
    }
  }

  depends_on = [
    terraform_data.image,
    google_secret_manager_secret_version.api_key,
    google_secret_manager_secret_iam_member.run_reads_secrets,
  ]
}

resource "google_cloud_run_v2_service_iam_member" "public" {
  count    = var.public ? 1 : 0
  name     = google_cloud_run_v2_service.api.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}
