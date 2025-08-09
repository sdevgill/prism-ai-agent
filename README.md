# Prism AI Agent

Prism AI Agent turns long-form written content into a coordinated multimedia campaign by orchestrating multiple AI services. Django anchors the web layer, **Celery** handles background orchestration, **Postgres** stores state, and **Redis** keeps task queues moving.

--------------------------------------------------------------------------------------

## Project Notes

The stack uses **Python 3.13**, **Django 5**, **Postgres 17**, **Redis 7**, **Celery**, **WhiteNoise**, and **Docker** with **uv** managing the virtual environment inside the image. Gunicorn fronts the web service in containers, while bind mounts keep source code and database files in the repository.

Setup notes:

- `src/settings.py`: single settings module reading `.env` values (DEBUG, ALLOWED_HOSTS, DATABASE_URL, REDIS_URL, DJANGO_SECRET_KEY) with sane fallbacks.
- `docker-compose.yml`: services for `web`, `worker`, `beat`, `db`, and `redis`, mounting the repo at `/var/www/prism-ai-agent` and persisting **Postgres** at `./data/postgres`.
- `Dockerfile`: **Python 3.13** slim image, installs build deps, and syncs dependencies via uv.

Data flow once features are in place will look like this: views accept content → orchestrator writes `Run` + `Step` rows → **Celery** tasks process steps and drop assets → UI polls for progress via HTMX.

--------------------------------------------------------------------------------------

## Setup with Docker

1. Install Docker Desktop or OrbStack (for Apple Silicon) with Compose v2 enabled.
2. Clone the repo and `cd prism-ai-agent`.
3. Copy the env template:
   ```bash
   cp .env.template .env
   ```
4. Start the stack (builds images and launches services):
   ```bash
   docker compose up --build
   ```
5. Apply database migrations:
   ```bash
   docker compose exec web python manage.py migrate
   ```
   (Runs automatically by default; set `SKIP_MIGRATE=true` to skip during startup. Static files collect on every boot.)
6. (Optional) Create an admin user:
   ```bash
   docker compose exec web python manage.py createsuperuser
   ```
7. Access the app at `http://localhost:8000/` once development views exist.
8. Stop services when you are done:
   ```bash
   docker compose down
   ```
   Add `-v` if you want to wipe the Postgres volume at `./data/postgres`.

--------------------------------------------------------------------------------------

## Daily Commands

Run everything from the host using Docker so container networking (hosts `db`, `redis`) resolves correctly:

- Make migrations: `docker compose exec web python manage.py makemigrations <app>`
- Apply migrations: `docker compose exec web python manage.py migrate`
- Run tests: `docker compose exec web python manage.py test`
- Collect static files: `docker compose exec web python manage.py collectstatic --noinput`
- Open a Django shell: `docker compose exec web python manage.py shell`
- Tail logs: `docker compose logs -f web` or `docker compose logs -f worker`
- Rebuild images after dependency edits: `docker compose build`

--------------------------------------------------------------------------------------

## Database Access From GUI Clients

Have the Postgres container running (`docker compose up db`) and use these settings in TablePlus, DataGrip, or any SQL client:

- Host: `localhost`
- Port: `5432`
- Database: `prism-ai-agent`
- User: `postgres`
- Password: `postgres`
- SSL: disabled / none

TablePlus: create a Postgres connection, fill in the values, click **Test**, then **Connect**. DataGrip: add a PostgreSQL datasource, enter the same values, press **Test Connection** (accept the driver download if prompted), then save the datasource.

--------------------------------------------------------------------------------------

## Dependency Stack

Application dependencies live in `pyproject.toml` and are installed inside the container’s uv-managed virtualenv at `/var/www/prism-ai-agent/.venv`. Core packages include Django, **Celery**, **Redis**, django-environ, django-htmx, django-tailwind-cli, psycopg2-binary, Pillow, **WhiteNoise**, and **gunicorn**. Optional tooling (**black**, **ruff**, **pre-commit**) sits under the `dev` extra for local linting/formatting if desired.

- Keep `./data/postgres` under version control ignore rules so databases persist between runs without polluting commits.
- Remove `./data/postgres` before restarting if you need a clean database.
- Keep `docker compose logs -f worker` running while developing Celery tasks to watch job output.
- Use `docker compose exec web bash` for an interactive shell inside the application container.
