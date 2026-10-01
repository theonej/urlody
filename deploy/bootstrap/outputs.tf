output "workload_identity_provider" {
  description = "GitHub variable GCP_WORKLOAD_IDENTITY_PROVIDER."
  value       = google_iam_workload_identity_pool_provider.github.name
}

output "deployer_service_account" {
  description = "GitHub variable GCP_DEPLOYER_SERVICE_ACCOUNT."
  value       = google_service_account.deployer.email
}

output "github_variables" {
  description = "Everything to set in the repository's develop environment."
  value       = <<-EOT
    GCP_PROJECT_ID                  ${var.project_id}
    GCP_REGION                      ${var.region}
    GCP_WORKLOAD_IDENTITY_PROVIDER  ${google_iam_workload_identity_pool_provider.github.name}
    GCP_DEPLOYER_SERVICE_ACCOUNT    ${google_service_account.deployer.email}
    MAILGUN_DOMAIN                  (your Mailgun sending domain)
    MAILGUN_FROM                    (optional sender address)
    secrets: MAILGUN_API_KEY, and optionally SCORER_API_KEY
  EOT
}
