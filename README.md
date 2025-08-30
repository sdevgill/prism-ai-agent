# Prism AI Agent

Prism AI Agent turns long-form written content or websites into a coordinated multimedia campaign by orchestrating multiple AI models. Django anchors the web layer, **Celery** handles background orchestration, **Postgres** stores state, and **Redis** keeps task queues moving.

--------------------------------------------------------------------------------------

## Project Notes

The stack uses **Python 3.13**, **Django 5**, **Postgres 17**, **Redis**, **Celery**, **Whitenoise**, and **Docker** with **uv** managing the virtual environment inside the image. **Gunicorn** fronts the web service in containers, while bind mounts keep source code and database files in the repository.

Setup notes:

- `src/settings.py`: single settings module reading `.env` values (DEBUG, ALLOWED_HOSTS, DATABASE_URL, REDIS_URL, DJANGO_SECRET_KEY, plus models specific settings) with sane fallbacks.
- `docker-compose.yml`: services for `web`, `worker`, `beat`, `db`, and `redis`, mounting the repo at `/var/www/prism-ai-agent` and persisting **Postgres** at `./data/postgres`.
- `Dockerfile`: **Python 3.13** slim image, installs build dependencies, and syncs dependencies via **uv**.

Data flow once features are in place will look like this: views accept content -> orchestrator writes `Run` + `Step` rows → **Celery** tasks process steps and drop assets → UI polls for progress via HTMX.

--------------------------------------------------------------------------------------

## Setup with Docker

1. Install Docker Desktop or OrbStack (for Apple Silicon).
2. Clone the repo and `cd prism-ai-agent`.
3. Copy the env template:
   ```bash
   cp .env.template .env
   ```
4. Start the containers (builds images and launches services):
   ```bash
   docker compose up --build
   ```
   This brings up the Django app, Celery workers, and runs `tailwind runserver` inside the web container so CSS rebuilds automatically in development.
5. (Optional) Apply database migrations:
   ```bash
   docker compose exec web python manage.py migrate
   ```
   (Runs automatically by default; set `SKIP_MIGRATE=true` to skip during startup. Static files collect on every boot.)
6. (Optional) Create an admin user:
   ```bash
   docker compose exec web python manage.py createsuperuser
   ```
7. Access the app at `http://localhost:8000/`.
8. Stop services when you are done:
   ```bash
   docker compose down
   ```
   Add `-v` if you want to wipe the Postgres volume at `./data/postgres`.

--------------------------------------------------------------------------------------

## Daily Commands

Run everything from the host using Docker so container networking (hosts `db`, `redis`) resolves correctly:

- Rebuild images after dependency edits: `docker compose build`
- Make migrations: `docker compose exec web python manage.py makemigrations <app>`
- Apply migrations: `docker compose exec web python manage.py migrate`
- Collect static files: `docker compose exec web python manage.py collectstatic --noinput`
- Open a Django shell: `docker compose exec web python manage.py shell`
- Tail logs: `docker compose logs -f web` or `docker compose logs -f worker`
- Sync dependencies when adding or updating Python packages: `uv sync` (the Docker entrypoint runs this automatically, but it’s handy for local virtualenvs).

 Tailwind CSS workflow (using `django-tailwind-cli`):

- `docker compose up` runs `python manage.py tailwind build` on startup followed by `python manage.py tailwind runserver`, so in the normal Docker flow you get the initial build and live watcher automatically.
- If you're working outside Docker (or just want to run it manually), bootstrap the CLI once with `python manage.py tailwind setup`.
- For an explicit rebuild, before `collectstatic` in CI, or if you’ve disabled the entrypoint build, run `docker compose exec web python manage.py tailwind build`.

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

Application dependencies live in `pyproject.toml` and are installed inside the container’s **uv**-managed virtualenv at `/var/www/prism-ai-agent/.venv`. Core packages include **Django**, **Celery**, **Redis**, **django-environ**, **django-htmx**, **django-tailwind-cli**, **psycopg2-binary**, **Pillow**, **Whitenoise**, **openai**, **tiktoken**, **google-genai**, and **gunicorn**. Optional tooling (**black**, **ruff**, **pre-commit**) sits under the `dev` extra for local linting/formatting if desired.

- Keep `./data/postgres` under version control ignore rules so databases persist between runs without polluting commits.
- Remove `./data/postgres` before restarting if you need a clean database.
- Keep `docker compose logs -f worker` running while developing Celery tasks to watch job output.
- Use `docker compose exec web bash` for an interactive shell inside the application container.

--------------------------------------------------------------------------------------

## Orchestration Flow

1. **Ingest:** Authenticated users name the run, provide either a URL or pasted copy, and tick image/audio/video checkboxes. The form persists modality-specific options (image count/quality/size, audio voice/format, video model/resolution) alongside the run.
2. **Prompting:** A Celery task calls **GPT-5** responses API with the run context. The system prompt is tailored to the selected modalities, including the audio guidance and Veo storyboard constraints so **GPT-5** returns `<modality>_prompt` JSON payloads. Prompts are saved as `Prompt` rows on the analyze step.
3. **Generation:** Based on the run’s requested modalities we enqueue image, audio, and video tasks. Each task reads the stored prompt plus the user's options, calls the provider API, and streams results to disk under `media/assets/{run_uuid}/{modality}/…`.
4. **Status + delivery:** The generate page polls an HTMX fragment that summarizes run/step progress (Analyze -> Image -> Audio -> Video). Successful generations create `Asset` rows that surface in the library with inline previews (images, `<audio>`, `<video>`) and download links.

## AI Models Configuration

- **Orchestrator – GPT-5 (OpenAI Responses API):** Produces the downstream prompts, injects audio/video specific instructions, and flags truncated source text. The run stores the raw JSON response as part of the prompt metadata.
- **Images – GPT-Image-1:** Honours count (1–3), quality (`low`/`medium`/`high`), and size (1024/1536 combos). Returns are decoded from `b64_json` into PNGs, and we persist provider/model/size metadata for display.
- **Audio – gpt-4o-mini-tts:** Generates narration with the selected voice (`ash`, `nova`, `ballad`) and format (`mp3` or `wav`). Duration and voice/format are stored so the UI can show playback controls plus metadata chips.
- **Video – Veo 3 Fast / Veo 3 (Google AI):** Defaults to `veo-3.0-fast-generate-001` at 720p for quick turnarounds, with `veo-3.0-generate-001` and 1080p available when users want more polish. The task polls the long-running operation, downloads the MP4, grabs the poster frame when provided, and records resolution/mime/duration.

Environment settings to set in `.env`:

- `OPENAI_API_KEY` (required) plus optional overrides: `OPENAI_RESPONSES_MODEL`, `OPENAI_IMAGE_MODEL`, `OPENAI_IMAGE_SIZE`, `OPENAI_IMAGE_QUALITY`, `OPENAI_AUDIO_MODEL`, `OPENAI_AUDIO_VOICE`, `OPENAI_AUDIO_FORMAT`, `OPENAI_AUDIO_SYSTEM_PROMPT`.
- `GOOGLE_API_KEY` (required for real Veo calls). Additional toggles: `GOOGLE_VEO_FAST_MODEL`, `GOOGLE_VEO_MODEL`, `GOOGLE_VEO_DEFAULT_RESOLUTION`, and `GOOGLE_VEO_POLL_INTERVAL`.
