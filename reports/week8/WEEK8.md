# Tuần 8 — Batch cloud

## Trạng thái

- Hoàn tất phần chuẩn bị offline: CLI, SQL publish, image, Terraform, IAM, Workflow, Scheduler và test mock
- Chưa triển khai hay chạy thật trên Google Cloud vì project `political-ads-intel-194276` không bật billing theo quyết định của chủ project
- Vì vậy chưa có cloud run IDs, log cloud, kiểm thử end-to-end không cần Mac, hoặc bằng chứng lịch hoạt động
- Không chạy `terraform apply`, `gcloud builds submit`, Cloud Run, Workflows hay Scheduler khi chưa chủ động quyết định ngân sách

## Luồng dữ liệu

```text
Cloud Scheduler (paused mặc định, thứ Tư 12:00 UTC)
  → Workflows ads-weekly
    → Cloud Run Job ads-extract
      → BigQuery public source (query có maximum_bytes_billed)
      → GCS raw/{week_start}/{run_id}/data.parquet + manifest.json
    → Cloud Run Job ads-publish
      → kiểm tra manifest, checksum, schema
      → BigQuery Bronze snapshot bất biến
      → quality gate + quarantine
      → Silver current/history + Gold marts trong transaction
      → ops.pipeline_runs PASS/FAIL
```

- `week_start` là Chủ nhật UTC; nếu không truyền thì CLI chọn tuần đã hoàn tất gần nhất
- Extract upload Parquet trước, manifest sau; tên object có run ID lấy từ phiên bản nguồn và query
- Publish chọn phiên bản nguồn mới nhất của tuần; cùng run ID đã PASS thì `SKIPPED`
- Quality gate lỗi sẽ không cập nhật Silver/Gold; quarantine và audit FAIL còn để điều tra
- Giới hạn một job/task, 1 vCPU, 1 GiB, 30 phút, 1 lần retry; hợp với máy local 8 GB vì xử lý chính ở BigQuery
- SQL Gold là bản cloud tương ứng marts local, chưa được xác nhận bằng BigQuery thật

## File chính

- `src/political_ads/cloud_batch.py`: CLI `extract` / `publish`, dry-run mặc định
- `src/political_ads/cloud_publish.py`: chọn/kiểm manifest, Bronze load, quality gate và publish
- `src/political_ads/sql/cloud/`: schema và SQL BigQuery
- `Dockerfile.cloud`: image Python 3.14 không chạy quyền root
- `cloudbuild.yaml`: build image bằng Dockerfile.cloud trên Cloud Build
- `infra/terraform/week8.tf`: APIs, Artifact Registry, service accounts, IAM, jobs, workflow, scheduler
- `infra/terraform/week8_workflow.yaml.tftpl`: chạy extract xong mới publish

## IAM dự kiến

| Identity | Quyền | Phạm vi |
| --- | --- | --- |
| `ads-extract` | `bigquery.jobUser` | Project |
| `ads-extract` | `storage.objectCreator`, `storage.objectViewer` | Raw bucket |
| `ads-publish` | `bigquery.jobUser` | Project |
| `ads-publish` | `bigquery.dataEditor` | Bronze, Silver, Gold, Ops datasets |
| `ads-publish` | `storage.objectViewer` | Raw bucket |
| `ads-workflow` | `run.jobsExecutorWithOverrides` | Chỉ hai Cloud Run Jobs |
| `ads-workflow` | `logging.logWriter` | Project |
| `ads-scheduler` | `workflows.invoker` | Project, vì role này không có binding cấp Workflow trong provider hiện dùng |
| Build identity nếu chỉ định | `artifactregistry.writer` | Chỉ repository image |

Người triển khai còn cần quyền Terraform tạo resource, `iam.serviceAccounts.actAs` trên runtime SAs và quyền build image; không cấp các quyền đó cho job khi chạy

## Kiểm tra offline đã chạy

```bash
python -m pip install -e '.[dev,cloud]'
make check
terraform -chdir=infra/terraform init -backend=false
terraform -chdir=infra/terraform fmt -check
terraform -chdir=infra/terraform validate
docker build -f Dockerfile.cloud -t political-ads-week8:offline .
docker run --rm --network none political-ads-week8:offline --help
```

- Test mock kiểm tra replay, checksum/schema drift, source correction, SQL quality gate trước transaction và audit FAIL
- Image chứa SQL đóng gói, đã smoke-test không cần mạng
- `terraform validate` không chứng minh resource tạo được trên project thực

## Khi chủ project chủ động bật billing sau này

Các lệnh dưới đây là runbook cho tương lai, **không được thực thi ở trạng thái hiện tại**

1. Xác nhận billing, ngân sách và budget alert, quota BigQuery; nhớ rằng budget alert không chặn chi tiêu
2. Tạo hạ tầng nền, vẫn tắt runtime và Scheduler

   ```bash
   terraform -chdir=infra/terraform plan -var='project_id=political-ads-intel-194276' -var='enable_gcs=true' -out=week8-base.tfplan
   terraform -chdir=infra/terraform apply week8-base.tfplan
   ```

3. Xác định Cloud Build identity bằng `gcloud builds get-default-service-account --project=political-ads-intel-194276`, cấp quyền ghi repository đã giới hạn; build image cho Cloud Run Linux

   ```bash
   gcloud builds submit . --project=political-ads-intel-194276 --region=us-central1 --config=cloudbuild.yaml --substitutions=_IMAGE=us-central1-docker.pkg.dev/political-ads-intel-194276/political-ads/batch:week8
   ```

   Nếu build identity không có quyền ghi Artifact Registry, cấp `roles/artifactregistry.writer` chỉ trên repository bằng biến `build_service_account_email` của Terraform rồi chạy lại

4. Lấy image digest `sha256:...`, rồi `terraform plan/apply` với `enable_runtime=true`, `enable_gcs=true`, `image_url=<repo/image@sha256:...>` và `schedule_paused=true`
5. Chạy Workflows thủ công cho một tuần: `gcloud workflows run ads-weekly --location=us-central1 --data='{"week_start":"2026-10-11"}'`
6. Kiểm tra execution, logs, `ops.pipeline_runs`, quarantine, và đối soát Bronze = valid + quarantine, Silver = Gold fact theo row count/spend
7. Chạy lại cùng tuần để kiểm tra không duplicate; thử một snapshot lỗi để chứng minh quality gate không publish
8. Chỉ khi các kiểm tra đạt mới chuyển `schedule_paused=false`; lúc tạm dừng đặt lại `true` và apply

Nếu bất kỳ bước nào thất bại, giữ lịch paused, đọc Cloud Run execution và Workflow execution logs, sửa nguyên nhân rồi chạy lại cùng logical week; không xóa raw snapshot hoặc audit để che lỗi

## Chi phí dự kiến nếu triển khai

- Ví dụ 4 kỳ/tháng, mỗi kỳ hai job chạy 10 phút: khoảng 4.800 vCPU-giây và 4.800 GiB-giây Cloud Run trước retry; còn dưới mức free tier công bố nếu billing account chưa dùng hết quota, **không bảo đảm hóa đơn bằng 0**
- Workflows vài chục step/tháng cũng thấp hơn mức free tier công bố; một Scheduler job có thể nằm trong 3 job miễn phí của billing account
- BigQuery query có `maximum_bytes_billed=256 MiB` mỗi job, nhưng nhiều job, storage, load, GCS, registry, logging, network và retry vẫn có thể phát sinh phí; kiểm tra Pricing Calculator trước khi triển khai
- Giá/free tier thay đổi; đối chiếu [Cloud Run](https://cloud.google.com/run/pricing), [Workflows](https://cloud.google.com/workflows/pricing), [Scheduler](https://cloud.google.com/scheduler/pricing), [BigQuery](https://cloud.google.com/bigquery/pricing), [Cloud Storage](https://cloud.google.com/storage/pricing), [Artifact Registry](https://cloud.google.com/artifact-registry/pricing) và [budget alerts](https://cloud.google.com/billing/docs/how-to/budgets)

## Tiêu chí còn thiếu để gọi là hoàn thành cloud

- Terraform apply tạo được bucket, datasets, image repo, jobs, workflow, scheduler mà không cấp quyền thừa
- Hai job chạy thật trên cloud, có execution IDs và log; Workflow chạy nguồn → Gold khi Mac tắt
- Rerun cùng logical week không nhân đôi Silver current/history; sửa nguồn tạo lịch sử mới
- Quarantine/quality failure chặn publish; lịch có thể pause/resume và mặc định paused sau deploy
- Đo thời gian/bytes/chi phí thực tế, rồi cập nhật báo cáo này bằng số liệu thật
