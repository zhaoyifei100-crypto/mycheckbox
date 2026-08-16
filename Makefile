# MyCheckBox PTSchool Cloud Run Job

PROJECT_ID ?= project-048627af-e7d4-4972-9d3
REGION ?= us-west1
JOB_NAME ?= mycheckbox
REPO_NAME ?= stock-study-repo
IMAGE_TAG ?= latest
COOKIE_KEY_SECRET_ID ?= mycheckbox-cookie-key
QQ_MAIL_ENCRYPTED_URL ?= https://raw.githubusercontent.com/zhaoyifei100-crypto/mycheckbox/refs/heads/main/secrets/qq_mail.enc
SERVICE_ACCOUNT ?= mycheckbox-sa@$(PROJECT_ID).iam.gserviceaccount.com
PTSCHOOL_UA ?=
SITES_FILE ?= sites.json
LOG_BUCKET ?= mycheckbox-logs
LOG_LOCATION ?= global
LOG_VIEW ?= _AllLogs
LOG_SINK ?= mycheckbox-to-bucket
LOG_RETENTION_DAYS ?= 30
SCHEDULE ?= 0 19 * * *
TIME_ZONE ?= Asia/Shanghai

IMAGE_PATH := $(REGION)-docker.pkg.dev/$(PROJECT_ID)/$(REPO_NAME)/$(JOB_NAME):$(IMAGE_TAG)
RUN_URI := https://$(REGION)-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/$(PROJECT_ID)/jobs/$(JOB_NAME):run

.PHONY: help build deploy run status logs executions setup-iam logging mail-secret-iam scheduler

help:
	@echo "MyCheckBox multi-site Cloud Run Job"
	@echo "make build       构建并部署镜像"
	@echo "make deploy      部署/更新 Cloud Run Job"
	@echo "make setup-iam   创建运行时账号并配置最小 IAM"
	@echo "make logging     创建专用 Cloud Logging bucket 和 sink"
	@echo "make mail-secret-iam 配置邮件 Secret 和日志读取权限"
	@echo "make scheduler   创建/更新每日 Scheduler"
	@echo "make run         手动执行一次 Job"
	@echo "make logs        查看脱敏应用日志"

check-config:
	@test -f "$(SITES_FILE)" || (echo "找不到 SITES_FILE=$(SITES_FILE)"; exit 1)

build: check-config
	gcloud builds submit --project $(PROJECT_ID) --tag $(IMAGE_PATH) --timeout=15m
	$(MAKE) deploy

deploy: check-config
	gcloud run jobs create $(JOB_NAME) \
		--project $(PROJECT_ID) \
		--region $(REGION) \
		--image $(IMAGE_PATH) \
		--service-account $(SERVICE_ACCOUNT) \
		--memory 512Mi \
		--cpu 1 \
		--max-retries 0 \
		--task-timeout 120s \
		--tasks 1 \
		--set-env-vars GCP_PROJECT_ID=$(PROJECT_ID),MYCHECKBOX_SITES_FILE=$(SITES_FILE),MYCHECKBOX_COOKIE_KEY_SECRET_ID=$(COOKIE_KEY_SECRET_ID),MYCHECKBOX_QQ_MAIL_ENCRYPTED_URL=$(QQ_MAIL_ENCRYPTED_URL),MYCHECKBOX_REPORT_TIME_ZONE=$(TIME_ZONE),MYCHECKBOX_LOG_BUCKET=$(LOG_BUCKET),MYCHECKBOX_LOG_LOCATION=$(LOG_LOCATION),MYCHECKBOX_LOG_VIEW=_AllLogs,MYCHECKBOX_JOB_NAME=$(JOB_NAME) \
		$(if $(PTSCHOOL_UA),--set-env-vars PTSCHOOL_UA=$(PTSCHOOL_UA),) \
		|| gcloud run jobs update $(JOB_NAME) \
		--project $(PROJECT_ID) \
		--region $(REGION) \
		--image $(IMAGE_PATH) \
		--service-account $(SERVICE_ACCOUNT) \
		--memory 512Mi \
		--cpu 1 \
		--max-retries 0 \
		--task-timeout 120s \
		--set-env-vars GCP_PROJECT_ID=$(PROJECT_ID),MYCHECKBOX_SITES_FILE=$(SITES_FILE),MYCHECKBOX_COOKIE_KEY_SECRET_ID=$(COOKIE_KEY_SECRET_ID),MYCHECKBOX_QQ_MAIL_ENCRYPTED_URL=$(QQ_MAIL_ENCRYPTED_URL),MYCHECKBOX_REPORT_TIME_ZONE=$(TIME_ZONE),MYCHECKBOX_LOG_BUCKET=$(LOG_BUCKET),MYCHECKBOX_LOG_LOCATION=$(LOG_LOCATION),MYCHECKBOX_LOG_VIEW=_AllLogs,MYCHECKBOX_JOB_NAME=$(JOB_NAME)

run:
	gcloud run jobs execute $(JOB_NAME) --project $(PROJECT_ID) --region $(REGION) --wait

status:
	gcloud run jobs describe $(JOB_NAME) --project $(PROJECT_ID) --region $(REGION)

logging:
	-gcloud logging buckets describe $(LOG_BUCKET) --project $(PROJECT_ID) --location $(LOG_LOCATION) >/dev/null 2>&1 || \
		gcloud logging buckets create $(LOG_BUCKET) --project $(PROJECT_ID) --location $(LOG_LOCATION) --retention-days $(LOG_RETENTION_DAYS) --description "Dedicated logs for MyCheckBox Cloud Run Job"
	-gcloud logging sinks describe $(LOG_SINK) --project $(PROJECT_ID) >/dev/null 2>&1 || \
		gcloud logging sinks create $(LOG_SINK) \
			logging.googleapis.com/projects/$(PROJECT_ID)/locations/$(LOG_LOCATION)/buckets/$(LOG_BUCKET) \
			--project $(PROJECT_ID) \
			--log-filter='resource.type="cloud_run_job" AND resource.labels.job_name="$(JOB_NAME)"' \
			--description "Route MyCheckBox Cloud Run Job logs to the dedicated bucket"

logs:
	gcloud logging read 'resource.type="cloud_run_job" AND resource.labels.job_name="$(JOB_NAME)" AND logName:"run.googleapis.com%2Fstdout"' \
		--project $(PROJECT_ID) --bucket $(LOG_BUCKET) --location $(LOG_LOCATION) --view=_AllLogs \
		--freshness=7d --limit=50 --order=desc \
		--format='table(timestamp.date(tz=Asia/Shanghai),labels."run.googleapis.com/execution_name",severity,textPayload)'

executions:
	gcloud run jobs executions list --job $(JOB_NAME) --project $(PROJECT_ID) --region $(REGION) --limit=10

setup-iam:
	-gcloud iam service-accounts describe $(SERVICE_ACCOUNT) --project $(PROJECT_ID) >/dev/null 2>&1 || \
		gcloud iam service-accounts create mycheckbox-sa --project $(PROJECT_ID) --display-name "MyCheckBox PTSchool Job"
	gcloud secrets add-iam-policy-binding $(COOKIE_KEY_SECRET_ID) \
		--project $(PROJECT_ID) \
		--member serviceAccount:$(SERVICE_ACCOUNT) \
		--role roles/secretmanager.secretAccessor

mail-secret-iam: setup-iam
	gcloud logging views add-iam-policy-binding $(LOG_VIEW) \
		--project $(PROJECT_ID) \
		--bucket $(LOG_BUCKET) \
		--location $(LOG_LOCATION) \
		--member serviceAccount:$(SERVICE_ACCOUNT) \
		--role roles/logging.viewAccessor

scheduler: setup-iam
	gcloud run jobs add-iam-policy-binding $(JOB_NAME) \
		--project $(PROJECT_ID) \
		--region $(REGION) \
		--member serviceAccount:$(SERVICE_ACCOUNT) \
		--role roles/run.invoker \
		--quiet
	-gcloud scheduler jobs delete $(JOB_NAME) --project $(PROJECT_ID) --location $(REGION) --quiet
	gcloud scheduler jobs create http $(JOB_NAME) \
		--project $(PROJECT_ID) \
		--location $(REGION) \
		--schedule "$(SCHEDULE)" \
		--time-zone "$(TIME_ZONE)" \
		--description "Daily MyCheckBox multi-site check-in" \
		--uri "$(RUN_URI)" \
		--http-method POST \
		--oauth-service-account-email $(SERVICE_ACCOUNT)
