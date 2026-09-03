.PHONY: install api worker work-once install-db status test lint fmt \
	up down logs ps docker-build docker-install docker-test docker-status

BACKEND := backend
COMPOSE := docker compose -f compose/docker-compose.yml

install:
	cd $(BACKEND) && poetry install

install-db:
	cd $(BACKEND) && poetry run cinqflow install

api:
	cd $(BACKEND) && poetry run uvicorn cinqflow.api.app:app --reload --port 8000

worker:
	cd $(BACKEND) && poetry run cinqflow work

work-once:
	cd $(BACKEND) && poetry run cinqflow work --once

status:
	cd $(BACKEND) && poetry run cinqflow status

test:
	cd $(BACKEND) && poetry run pytest -v

lint:
	cd $(BACKEND) && poetry run ruff check src tests

fmt:
	cd $(BACKEND) && poetry run ruff format src tests

# --- Docker orchestration (compose/docker-compose.yml) ----------------------
# Cold start: postgres -> migrate (idempotent `cinqflow install`) -> api/worker
# (both wait on migrate) -> frontend (waits on api's healthcheck).

docker-build:
	$(COMPOSE) build

up:
	$(COMPOSE) up --build

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f

ps:
	$(COMPOSE) ps

docker-status:
	$(COMPOSE) exec api poetry run cinqflow status

# Re-runs the idempotent schema install on demand (compose already runs this
# once via the `migrate` service on every `up`).
docker-install:
	$(COMPOSE) run --rm migrate

# Runs the real pytest suite inside the api image, against the compose
# Postgres, using CINQFLOW_TEST_DATABASE_URL (tests/conftest.py) so it never
# touches the dev schemas the running api/worker use.
docker-test:
	$(COMPOSE) run --rm \
		-e CINQFLOW_TEST_DATABASE_URL=postgresql://$${POSTGRES_USER:-cinqflow}:$${POSTGRES_PASSWORD:-cinqflow}@postgres:5432/$${POSTGRES_DB:-cinqflow} \
		api poetry run pytest -v
