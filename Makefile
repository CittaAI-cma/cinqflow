# ─────────────────────────────────────────────────────────────────────────────
# cinqflow — the one entry point to the orchestration.
#
#   make up ENV=local      the twin, bind-mounted, reloading
#   make up ENV=dev        the twin, built and closed down
#   make up ENV=prod       the app tier only; infra is the client's
#
# ENV picks the overlay AND the profile, together, because they are the same
# decision: what the environment IS and what the platform is TOLD it is must
# not be settable separately, or a prod container ends up loading the laptop's
# profile and pointing at a database somebody's laptop can reach.
#
# `--project-directory ..` is not cosmetic: compose interpolates ${VARS} from
# the .env in the PROJECT directory, and the project directory is the repo
# root, where the one .env lives.
# ─────────────────────────────────────────────────────────────────────────────
ENV ?= local

COMPOSE := docker compose --project-directory . \
	-f compose/docker-compose.yml \
	-f compose/docker-compose.$(ENV).yml

.DEFAULT_GOAL := help

.PHONY: help
help:  ## the targets, and what each one is for
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "  ENV=$(ENV)   (local | dev | prod)"

.PHONY: env
env:  ## project .env into compose/secrets/ — every other target depends on it
	@./compose/bootstrap-env.sh

.PHONY: config
config: env  ## the fully-resolved compose file. Read this before trusting `up`.
	@$(COMPOSE) config

.PHONY: build
build: env  ## build the two app images for ENV
	$(COMPOSE) build

.PHONY: up
up: env  ## bring ENV up in the background
	$(COMPOSE) up -d --remove-orphans
	@echo ""
	@$(COMPOSE) ps

.PHONY: down
down:  ## stop ENV. Volumes SURVIVE — see `make nuke` for the other thing.
	$(COMPOSE) down --remove-orphans

.PHONY: logs
logs:  ## follow every service's logs
	$(COMPOSE) logs -f --tail=100

.PHONY: ps
ps:  ## what is running, and whether it is healthy
	@$(COMPOSE) ps

.PHONY: restart
restart:  ## restart just the app tier, leaving the databases up
	$(COMPOSE) restart backend frontend

.PHONY: shell
shell:  ## a shell in the backend container
	$(COMPOSE) exec backend sh

.PHONY: psql-platform
psql-platform:  ## psql into the PLATFORM plane — registry, control, queue, vectors
	$(COMPOSE) exec postgres-platform psql -U $$(grep '^CINQFLOW_PG_PLATFORM_USER=' .env | cut -d= -f2) -d $$(grep '^CINQFLOW_PG_PLATFORM_DB=' .env | cut -d= -f2)

.PHONY: psql-data
psql-data:  ## psql into the DATA plane — bronze, silver, gold
	$(COMPOSE) exec postgres-data psql -U $$(grep '^CINQFLOW_PG_DATA_USER=' .env | cut -d= -f2) -d $$(grep '^CINQFLOW_PG_DATA_DB=' .env | cut -d= -f2)

.PHONY: install
install: ## run the installer against ENV's profile — creates the schemas
	$(COMPOSE) exec backend cinqflow install --profile profiles/$(ENV).yaml

.PHONY: nuke
nuke:  ## stop ENV and DELETE ITS VOLUMES. The platform plane is pinned by name and is NOT touched by dev/prod.
	@echo "This deletes $(ENV)'s data volumes. Ctrl-C within 5s to stop."
	@sleep 5
	$(COMPOSE) down --volumes --remove-orphans
