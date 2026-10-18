terraform {
  required_version = ">= 1.8, < 2.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 8.0"
    }
  }
}

variable "project_id" {
  description = "Google Cloud project dùng cho platform"
  type        = string
}

variable "enable_gcs" {
  description = "Chỉ bật khi đã quyết định dùng Cloud Storage và kiểm tra billing"
  type        = bool
  default     = false
}

provider "google" {
  project = var.project_id
  region  = "us-central1"
}

resource "google_bigquery_dataset" "bronze" {
  dataset_id                 = "political_ads_bronze"
  location                   = "US"
  delete_contents_on_destroy = false

  labels = {
    project = "political_ads"
    layer   = "bronze"
  }
}

resource "google_storage_bucket" "raw" {
  count = var.enable_gcs ? 1 : 0

  name                        = "${var.project_id}-political-ads-raw"
  location                    = "US"
  uniform_bucket_level_access = true
  force_destroy               = false
}