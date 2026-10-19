variable "region" {
  description = "Region for Cloud Run Jobs, Workflows, Scheduler and Artifact Registry"
  type        = string
  default     = "us-central1"
}

variable "enable_runtime" {
  description = "Create paid cloud runtime only after billing and image are ready"
  type        = bool
  default     = false
}

variable "image_url" {
  description = "Immutable Artifact Registry image digest for both batch jobs"
  type        = string
  default     = ""
}

variable "schedule_paused" {
  description = "Keep the weekly schedule paused until manual checks pass"
  type        = bool
  default     = true
}

variable "build_service_account_email" {
  description = "Optional Cloud Build identity to grant Artifact Registry Writer"
  type        = string
  default     = ""
}

locals {
  raw_bucket_name = "${var.project_id}-political-ads-raw"
  runtime_sas = {
    extract = google_service_account.extract.email
    publish = google_service_account.publish.email
  }
}

resource "google_project_service" "required" {
  for_each = toset([
    "artifactregistry.googleapis.com",
    "bigquery.googleapis.com",
    "cloudbuild.googleapis.com",
    "cloudscheduler.googleapis.com",
    "iam.googleapis.com",
    "logging.googleapis.com",
    "run.googleapis.com",
    "storage.googleapis.com",
    "workflowexecutions.googleapis.com",
    "workflows.googleapis.com",
  ])

  project            = var.project_id
  service            = each.key
  disable_on_destroy = false
}

resource "google_artifact_registry_repository" "batch" {
  repository_id = "political-ads"
  location      = var.region
  format        = "DOCKER"
  description   = "Weekly political ads batch image"

  depends_on = [google_project_service.required]
}

resource "google_service_account" "extract" {
  account_id   = "ads-extract"
  display_name = "Political ads raw extractor"
  depends_on   = [google_project_service.required]
}

resource "google_service_account" "publish" {
  account_id   = "ads-publish"
  display_name = "Political ads warehouse publisher"
  depends_on   = [google_project_service.required]
}

resource "google_service_account" "workflow" {
  account_id   = "ads-workflow"
  display_name = "Political ads batch orchestrator"
  depends_on   = [google_project_service.required]
}

resource "google_service_account" "scheduler" {
  account_id   = "ads-scheduler"
  display_name = "Political ads weekly schedule"
  depends_on   = [google_project_service.required]
}

resource "google_project_iam_member" "bigquery_job_user" {
  for_each = local.runtime_sas
  project  = var.project_id
  role     = "roles/bigquery.jobUser"
  member   = "serviceAccount:${each.value}"
}

resource "google_bigquery_dataset_iam_member" "publisher_editor" {
  for_each = {
    bronze = google_bigquery_dataset.bronze.dataset_id
    silver = google_bigquery_dataset.silver.dataset_id
    gold   = google_bigquery_dataset.gold.dataset_id
    ops    = google_bigquery_dataset.ops.dataset_id
  }

  project    = var.project_id
  dataset_id = each.value
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.publish.email}"
}

resource "google_storage_bucket_iam_member" "extract_creator" {
  count  = var.enable_gcs ? 1 : 0
  bucket = google_storage_bucket.raw[0].name
  role   = "roles/storage.objectCreator"
  member = "serviceAccount:${google_service_account.extract.email}"
}

resource "google_storage_bucket_iam_member" "extract_viewer" {
  count  = var.enable_gcs ? 1 : 0
  bucket = google_storage_bucket.raw[0].name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.extract.email}"
}

resource "google_storage_bucket_iam_member" "publish_viewer" {
  count  = var.enable_gcs ? 1 : 0
  bucket = google_storage_bucket.raw[0].name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.publish.email}"
}

resource "google_artifact_registry_repository_iam_member" "builder_writer" {
  count      = var.build_service_account_email == "" ? 0 : 1
  project    = var.project_id
  location   = var.region
  repository = google_artifact_registry_repository.batch.repository_id
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${var.build_service_account_email}"
}

resource "google_project_iam_member" "workflow_log_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.workflow.email}"
}

resource "google_project_iam_member" "scheduler_invoker" {
  project = var.project_id
  role    = "roles/workflows.invoker"
  member  = "serviceAccount:${google_service_account.scheduler.email}"
}

resource "google_cloud_run_v2_job" "extract" {
  count               = var.enable_runtime ? 1 : 0
  name                = "ads-extract"
  location            = var.region
  deletion_protection = false

  template {
    task_count  = 1
    parallelism = 1

    template {
      service_account = google_service_account.extract.email
      timeout         = "1800s"
      max_retries     = 1

      containers {
        image = var.image_url
        args  = ["extract", "--execute"]

        resources {
          limits = {
            cpu    = "1"
            memory = "1Gi"
          }
        }

        env {
          name  = "GCP_PROJECT_ID"
          value = var.project_id
        }
        env {
          name  = "RAW_BUCKET"
          value = local.raw_bucket_name
        }
        env {
          name  = "MAX_QUERY_MIB"
          value = "256"
        }
      }
    }
  }

  lifecycle {
    precondition {
      condition = var.enable_gcs && startswith(
        var.image_url,
        "${var.region}-docker.pkg.dev/${var.project_id}/political-ads/"
      )
      error_message = "Runtime needs enable_gcs=true and an Artifact Registry image_url"
    }
  }

  depends_on = [google_project_service.required]
}

resource "google_cloud_run_v2_job" "publish" {
  count               = var.enable_runtime ? 1 : 0
  name                = "ads-publish"
  location            = var.region
  deletion_protection = false

  template {
    task_count  = 1
    parallelism = 1

    template {
      service_account = google_service_account.publish.email
      timeout         = "1800s"
      max_retries     = 1

      containers {
        image = var.image_url
        args  = ["publish", "--execute"]

        resources {
          limits = {
            cpu    = "1"
            memory = "1Gi"
          }
        }

        env {
          name  = "GCP_PROJECT_ID"
          value = var.project_id
        }
        env {
          name  = "RAW_BUCKET"
          value = local.raw_bucket_name
        }
        env {
          name  = "MAX_QUERY_MIB"
          value = "256"
        }
      }
    }
  }

  lifecycle {
    precondition {
      condition = var.enable_gcs && startswith(
        var.image_url,
        "${var.region}-docker.pkg.dev/${var.project_id}/political-ads/"
      )
      error_message = "Runtime needs enable_gcs=true and an Artifact Registry image_url"
    }
  }

  depends_on = [google_project_service.required]
}

resource "google_cloud_run_v2_job_iam_member" "workflow_extract" {
  count    = var.enable_runtime ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_job.extract[0].name
  role     = "roles/run.jobsExecutorWithOverrides"
  member   = "serviceAccount:${google_service_account.workflow.email}"
}

resource "google_cloud_run_v2_job_iam_member" "workflow_publish" {
  count    = var.enable_runtime ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_job.publish[0].name
  role     = "roles/run.jobsExecutorWithOverrides"
  member   = "serviceAccount:${google_service_account.workflow.email}"
}

resource "google_workflows_workflow" "weekly" {
  count               = var.enable_runtime ? 1 : 0
  name                = "ads-weekly"
  region              = var.region
  description         = "Extract, quality-gate and publish weekly political ads"
  service_account     = google_service_account.workflow.id
  call_log_level      = "LOG_ERRORS_ONLY"
  deletion_protection = false
  source_contents = templatefile("${path.module}/week8_workflow.yaml.tftpl", {
    project_id = var.project_id
    region     = var.region
  })

  depends_on = [
    google_cloud_run_v2_job_iam_member.workflow_extract,
    google_cloud_run_v2_job_iam_member.workflow_publish,
  ]
}

resource "google_cloud_scheduler_job" "weekly" {
  count            = var.enable_runtime ? 1 : 0
  name             = "ads-weekly"
  region           = var.region
  description      = "Weekly political ads batch; initially paused"
  schedule         = "0 12 * * 3"
  time_zone        = "Etc/UTC"
  paused           = var.schedule_paused
  attempt_deadline = "180s"

  retry_config {
    retry_count = 0
  }

  http_target {
    http_method = "POST"
    uri         = "https://workflowexecutions.googleapis.com/v1/projects/${var.project_id}/locations/${var.region}/workflows/ads-weekly/executions"
    body        = base64encode("{}")
    headers = {
      "Content-Type" = "application/json"
    }

    oauth_token {
      service_account_email = google_service_account.scheduler.email
    }
  }

  depends_on = [
    google_workflows_workflow.weekly,
    google_project_iam_member.scheduler_invoker,
  ]
}

output "cloud_batch_resources" {
  description = "Resource names are populated only when enable_runtime=true"
  value = {
    image_repository = google_artifact_registry_repository.batch.id
    extract_job      = try(google_cloud_run_v2_job.extract[0].id, null)
    publish_job      = try(google_cloud_run_v2_job.publish[0].id, null)
    workflow         = try(google_workflows_workflow.weekly[0].id, null)
    scheduler        = try(google_cloud_scheduler_job.weekly[0].id, null)
    scheduler_paused = var.schedule_paused
  }
}
