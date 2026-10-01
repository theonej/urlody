variable "project_id" {
  description = "Google Cloud project to deploy into."
  type        = string
}

variable "region" {
  description = "Region for Cloud Run and the image repository."
  type        = string
  default     = "us-central1"
}

variable "service_name" {
  description = "Name of the Cloud Run service (and of the image)."
  type        = string
  default     = "scorer"
}

variable "mailgun_api_key" {
  description = "Mailgun domain sending key (or private API key); stored in Secret Manager."
  type        = string
  sensitive   = true
}

variable "mailgun_domain" {
  description = "Mailgun sending domain, e.g. mail.example.com."
  type        = string
}

variable "mailgun_from" {
  description = "Sender address; defaults to scorer@<mailgun_domain>."
  type        = string
  default     = ""
}

variable "mailgun_api_base" {
  description = "Mailgun API base; use https://api.eu.mailgun.net for EU accounts."
  type        = string
  default     = "https://api.mailgun.net"
}

variable "api_key" {
  description = "Value callers must send in X-API-Key. A random one is generated when empty."
  type        = string
  sensitive   = true
  default     = ""
}

variable "public" {
  description = "Let anyone on the internet reach the service (callers still need the API key)."
  type        = bool
  default     = true
}

variable "cpu" {
  description = "vCPUs per instance. Transcription is CPU-bound."
  type        = string
  default     = "2"
}

variable "memory" {
  description = "Memory per instance. Audio analysis of a few minutes of music peaks near 2 GiB."
  type        = string
  default     = "4Gi"
}

variable "workers" {
  description = "Transcriptions one instance runs at once (SCORER_API_WORKERS)."
  type        = number
  default     = 1
}

variable "min_instances" {
  description = "Instances kept warm. Keep at least 1: the transcription runs after the response, and an instance scaled to zero takes its unfinished jobs with it."
  type        = number
  default     = 1
}

variable "max_instances" {
  description = "Most instances Cloud Run may add under load."
  type        = number
  default     = 2
}

variable "build_image" {
  description = "Build and push the image with Cloud Build from this repository on apply (needs gcloud). Set false to supply a prebuilt image."
  type        = bool
  default     = true
}

variable "image" {
  description = "Prebuilt image to run when build_image is false."
  type        = string
  default     = ""
}
