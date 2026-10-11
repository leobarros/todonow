# todonow — atalhos para o dia a dia no homelab
SHELL := /bin/bash
COMPOSE := docker compose
APP := todonow
# Imagem padrão = a publicada no Docker Hub, na tag latest (o compose sempre
# confere se saiu versão nova). Sobrescreva com TODONOW_IMAGE p/ fixar versão.
REGISTRY ?= nickolback
IMAGE_NAME ?= todonow
TAG ?= latest
IMAGE ?= $(REGISTRY)/$(IMAGE_NAME):$(TAG)
BUILD_COMPOSE := -f docker-compose.yml -f docker-compose.build.yml

.DEFAULT_GOAL := help

.PHONY: help
help: ## Mostra esta ajuda
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

.PHONY: env
env: ## Cria .env a partir do exemplo (se ainda não existir)
	@test -f .env || (cp .env.example .env && echo "Crie .env a partir de .env.example e ajuste SECRET_KEY")

.PHONY: up
up: env ## Baixa a latest publicada e sobe o app (recria se mudou)
	$(COMPOSE) up -d

.PHONY: build
build: env ## Constrói a imagem localmente a partir do código-fonte
	$(COMPOSE) $(BUILD_COMPOSE) build

.PHONY: up-build
up-build: env ## Constrói da fonte e sobe (sem usar a imagem publicada)
	$(COMPOSE) $(BUILD_COMPOSE) up -d --build

.PHONY: push
push: ## Publica a imagem no Docker Hub (TAG=latest por padrão)
	$(COMPOSE) $(BUILD_COMPOSE) build
	docker push $(IMAGE)

.PHONY: release
release: ## Publica a latest junto com uma tag de versão fixa (VERSION=1.1.0)
	$(COMPOSE) $(BUILD_COMPOSE) build
	docker tag $(IMAGE) $(REGISTRY)/$(IMAGE_NAME):$(VERSION)
	docker push $(IMAGE)
	docker push $(REGISTRY)/$(IMAGE_NAME):$(VERSION)

.PHONY: down
down: ## Para o app
	$(COMPOSE) down

.PHONY: restart
restart: ## Reinicia o app
	$(COMPOSE) restart

.PHONY: logs
logs: ## Acompanha os logs
	$(COMPOSE) logs -f --tail=100

.PHONY: ps
ps: ## Mostra o status e o consumo de recursos
	$(COMPOSE) ps
	docker stats --no-stream $(APP) || true

.PHONY: shell
shell: ## Abre um shell dentro do container
	$(COMPOSE) exec $(APP) /bin/sh

.PHONY: create-admin
create-admin: ## Cria/atualiza um admin (use USER=... PASS=...)
	$(COMPOSE) exec $(APP) flask --app wsgi create-user "$${USER:-admin}" --password "$${PASS:-changeme123}" --admin

.PHONY: seed
seed: ## Popula a conta USER com tarefas de exemplo
	$(COMPOSE) exec $(APP) flask --app wsgi seed-demo "$${USER:-admin}"

.PHONY: backup
backup: ## Backup completo da instância em ./backups
	@mkdir -p backups
	$(COMPOSE) exec -T $(APP) flask --app wsgi backup --scope full --output /data/backups/manual-$$(date +%Y%m%d-%H%M%S).json
	$(COMPOSE) cp $(APP):/data/backups/. ./backups/
	@ls -lh backups | tail -5

.PHONY: snapshots
snapshots: ## Lista os snapshots guardados no volume
	$(COMPOSE) exec $(APP) flask --app wsgi snapshots

.PHONY: install
install: ## Instala as dependências localmente (venv)
	python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt

.PHONY: run
run: ## Roda o app localmente (sem Docker) em modo debug
	FLASK_ENV=development .venv/bin/flask --app wsgi run --debug --host 127.0.0.1 --port 8081

.PHONY: dev
dev: ## Sobe em modo desenvolvimento (código montado + reload)
	$(COMPOSE) $(BUILD_COMPOSE) -f docker-compose.dev.yml up -d --build

.PHONY: test
test: ## Executa a suíte de testes
	.venv/bin/pytest -q

.PHONY: lint
lint: ## Roda o ruff
	.venv/bin/ruff check .

.PHONY: fmt
fmt: ## Formata o código com ruff
	.venv/bin/ruff format .

.PHONY: migrate
migrate: ## Gera uma migração nova (MSG="descrição")
	.venv/bin/flask --app wsgi db migrate -m "$${MSG:-migração}"

.PHONY: upgrade
upgrade: ## Aplica as migrações pendentes no banco local
	.venv/bin/flask --app wsgi db upgrade

.PHONY: clean
clean: ## Remove caches locais
	rm -rf .pytest_cache .ruff_cache htmlcov .coverage
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
