output "url" {
  description = "Base URL of the API; POST to <url>/transcriptions."
  value       = google_cloud_run_v2_service.api.uri
}

output "image" {
  description = "Container image the service runs."
  value       = local.image
}

output "api_key" {
  description = "Value callers must send in the X-API-Key header."
  value       = local.api_key
  sensitive   = true
}

output "example" {
  description = "A request to try, once you have the key from `terraform output -raw api_key`."
  value       = <<-EOT
    curl -X POST ${google_cloud_run_v2_service.api.uri}/transcriptions \
      -H 'Content-Type: application/json' -H "X-API-Key: $(terraform output -raw api_key)" \
      -d '{"url": "https://www.youtube.com/watch?v=...", "email": "you@example.com", "split": true}'
  EOT
}
