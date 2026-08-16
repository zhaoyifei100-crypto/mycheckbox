# MyCheckBox PTSchool Cloud Run Job

PROJECT_ID ?= project-048627af-e7d4-4972-9d3
REGION ?= us-west1
JOB_NAME ?= mycheckbox
REPO_NAME ?= stock-study-repo
IMAGE_TAG ?= latest
COOKIE_KEY_SECRET_ID ?= mycheckbox-cookie-key
SERVICE_ACCOUNT ?= mycheckbox-sa@$(PROJECT_ID).iam.gserviceaccount.com
PTSCHOOL_UA ?=
SITES_FILE ?= sites.json
SCHEDULE ?= 0 19 * * *
TIME_ZONE ?= America/New_York

IMAGE_PATH := $(REGION)-docker.pkg.dev/$(PROJECT_ID)/$(REPO_NAME)/$(JOB_NAME):$(IMAGE_TAG)
RUN_URI := https://$(REGION)-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/$(PROJECT_ID)/jobs/$(JOB_NAME):run

.PHONY: help build deploy run status logs executions setup-iam scheduler

help:
	@echo "MyCheckBox multi-site Cloud Run Job"
	@echo "make build       构建并部署镜像"
	@echo "make deploy      部署/更新 Cloud Run Job"
	@echo "make setup-iam   创建运行时账号并配置最小 IAM"
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
		--set-env-vars GCP_PROJECT_ID=$(PROJECT_ID),MYCHECKBOX_SITES_FILE=$(SITES_FILE),MYCHECKBOX_COOKIE_KEY_SECRET_ID=$(COOKIE_KEY_SECRET_ID) \
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
		--set-env-vars GCP_PROJECT_ID=$(PROJECT_ID),MYCHECKBOX_SITES_FILE=$(SITES_FILE),MYCHECKBOX_COOKIE_KEY_SECRET_ID=$(COOKIE_KEY_SECRET_ID)

run:
	gcloud run jobs execute $(JOB_NAME) --project $(PROJECT_ID) --region $(REGION) --wait

status:
	gcloud run jobs describe $(JOB_NAME) --project $(PROJECT_ID) --region $(REGION)

logs:
	gcloud logging read 'resource.type="cloud_run_job" AND resource.labels.job_name="$(JOB_NAME)"' \
		--project $(PROJECT_ID) --freshness=7d --limit=50 --format='value(textPayload)'

executions:
	gcloud run jobs executions list --job $(JOB_NAME) --project $(PROJECT_ID) --region $(REGION) --limit=10

setup-iam:
	-gcloud iam service-accounts describe $(SERVICE_ACCOUNT) --project $(PROJECT_ID) >/dev/null 2>&1 || \
		gcloud iam service-accounts create mycheckbox-sa --project $(PROJECT_ID) --display-name "MyCheckBox PTSchool Job"
	gcloud projects add-iam-policy-binding $(PROJECT_ID) \
		--member serviceAccount:$(SERVICE_ACCOUNT) \
		--role roles/run.invoker \
		--condition=None --quiet
	gcloud secrets add-iam-policy-binding $(COOKIE_KEY_SECRET_ID) \
		--project $(PROJECT_ID) \
		--member serviceAccount:$(SERVICE_ACCOUNT) \
		--role roles/secretmanager.secretAccessor

scheduler: setup-iam
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
