# SmartSeat

Teacher and student portals for exam planning, with locally processed classroom images and optional Groq assistance. Real classroom/camera testing and the functional test suite are deferred at the user's request for this expansion. Build checks and Docker startup are separate implementation checks.

## Run locally with Docker Desktop

1. Open Docker Desktop and wait until its Linux engine is running.
2. A private `.env` has been created with a generated database password. To activate the assistant, edit it and set `GROQ_API_KEY` to your Groq key. Never put this key into a chat form, source file or screenshot. `GROQ_MODEL` defaults to `openai/gpt-oss-20b`.
3. From the project root:

```powershell
docker compose up -d --build
```

4. Open **http://localhost:8080**. Create the first teacher account if starting fresh. The username is ADMIN and you choose the password.

The first build downloads the CPU AI runtime. The first image detection downloads model weights into a persistent Docker volume. Original videos are decoded in your browser. Photos/frames are processed by your local Docker backend; they are not sent to Groq. The Groq assistant is an optional external service and needs internet plus your own API key.

The web server is bound to your computer's loopback interface. PostgreSQL, Redis and the API are private to the Compose network. Docker is for local use; no domain or public hosting is configured.

### Preserve the existing non-Docker project data

Docker uses its own PostgreSQL database. Your earlier SQLite database is kept unchanged and mounted read-only for an explicit one-time import. Before creating accounts in the fresh Docker database, run:

```powershell
docker compose exec backend python -m backend.transfer
```

The import refuses to overwrite an existing user database. It preserves existing teacher/student password hashes, classrooms, exams, layouts and attendance. Login sessions are not copied. Sign in again using your existing credentials. If you already created a Docker account, keep that database and migrate deliberately; do not delete volumes just to retry.

### Stop, restart and update configuration

```powershell
docker compose stop
docker compose up -d
# After changing GROQ_API_KEY or another environment setting:
docker compose up -d --force-recreate backend
# After editing application source:
docker compose up -d --build
```

Persistent volumes retain records and model downloads. Do not use `down -v` unless you intend to erase the Docker data.

## Daily workflow

1. Add/import students once. Required CSV/Excel columns: `usn,name,branch`. Passwords are optional and generate automatically when omitted. Download login slips immediately after creation; share each slip privately. Students must change their temporary password at first login. Duplicate rows reject the entire import rather than silently resetting existing accounts.
2. Add classrooms manually or through photo/video/camera mapping. Save layouts once and reuse them.
3. Create an exam, set paper codes once per branch, and select classrooms. The advanced section allows individual student selection and paper overrides. **Create and prepare seats** runs the solver immediately. If the selected layout cannot fit the strict rules, neither exam nor allocation is saved.
4. Review the chart, then publish separately. Students see only their own published allocation and refresh every 30 seconds. Unpublish before editing/regenerating. Regeneration clears attendance because the seating plan has changed.
5. Optional: adjust or swap draft seats using the expandable section in the exam. Every change rechecks all strict rules and accessibility requirements.
6. Download PDF or Excel seating lists. Use Manage > Exams & attendance to mark attendance and export CSV.

Paper code, not branch alone, determines separation. Students writing the same paper must share a code even if their branches differ. Room capacity alone does not guarantee a feasible plan.

## Flexible layouts and management

Manage > Rooms > Edit layout provides a seat grid. Click a seat to cycle between usable, accessible and unavailable. Mark all seats on a missing bench unavailable; disable spare seats on smaller benches. This supports uneven rows, missing benches and variable bench capacities within a logical grid of at most 20 rows, 12 benches per row and 3 seats per bench.

Add extra separation links for seats facing each other or nearby. The solver enforces these in addition to same-bench separation and optional matching-position front/back separation. Physical adjacency must be confirmed by the teacher; the image model does not establish it automatically.

Manage > Students allows editing records and marking students as requiring an accessible seat. The solver reserves accessible placements for those students; it reports infeasibility rather than placing them in an unsuitable seat. No medical details are stored or sent to the assistant. Records used by a published exam must be unpublished before editing. Changes to room layouts or students invalidate affected draft seat plans.

Password resets create a fresh temporary password, display it once to the teacher, revoke previous sessions and require a password change. For teacher recovery with local Docker owner access, run `docker compose exec -it backend python -m backend.recover_teacher`. It prompts privately for a new password, revokes sessions and records recovery in the audit log. Keep your teacher password and backups securely.

Manage > Students also provides **Delete** beside Archive. Permanent deletion requires typing the USN in a confirmation dialog. It removes the student account, sessions, support settings, draft enrollments and related attendance, and clears affected draft seating. Students linked to published exams cannot be deleted until those exams are unpublished. Deleted students cannot be restored from Archive.

Archive provides reversible removal for students, rooms and exams. Archived records are hidden from normal lists and cannot be newly selected. Student sessions are revoked when archived. Restore records from Manage > Archive. Published exam references block archiving to preserve seating charts.

Manage > Audit shows the latest 200 successful-operation/management audit entries, with actor, action, target and time. Passwords, API keys and request bodies are never stored in audit entries.

## Groq + LangChain teacher assistant

After configuring `GROQ_API_KEY`, open Assistant. It receives your message and a limited campus summary: branch counts, room names/capacities, current time and published-exam count. Student names, USNs, passwords, accessibility flags and classroom images are excluded from that summary. Avoid including student personal details in your own prompts.

Example: "Prepare an exam for CSE and ECE on 20 December 2026 at 9 am IST. CSE paper CS301, ECE paper EC301. Duration 180 minutes."

The model produces a structured proposal. You can edit its details and paper codes, then explicitly approve draft creation. The backend validates it and uses the seating solver; the resulting exam remains unpublished. The assistant cannot publish, archive, reset passwords or run arbitrary SQL/code. Missing keys and upstream errors are shown honestly. No live Groq request has been verified without your API key.

## Classroom photos, video and live camera

- Photo: upload JPEG/PNG up to 8 MB and 20 megapixels. Pretrained Grounding DINO finds classroom desks as one desk-and-bench unit each. No custom training is required.
- Recorded video: upload a browser-supported MP4/WebM/MOV clip of 1-120 seconds, at most 100 MB. Four frames are extracted locally. Select the clearest full-room view or choose **Combine overlapping frames**.
- Overlapping-frame mapping: OpenCV attempts a geometrically aligned panorama before detection. It never adds separate frame counts. Frames need adequate overlap, preferably a camera rotated from one position. The backend rejects failed alignment. Moving walkthroughs with strong parallax can fail or produce duplicated/distorted objects; review seams before saving. This is not guaranteed 3D room reconstruction.
- Live camera: start it yourself, approve the browser request, and capture a view. Audio is never requested. Tracks stop after capture, stopping, changing modes, closing the panel or leaving the page. If the in-app browser lacks camera support, use Chrome or Edge at the same localhost address.

Review detection boxes, remove incorrect ones and mark missing units. Confirm rows, units per row, seats per unit and orientation before saving. The initial photo save uses a rectangular layout; use Manage > Rooms to adapt uneven rows or missing seats afterward. Images do not reliably establish capacity or physical adjacency. Real classroom accuracy evaluation is deferred.

## Data migrations, security and backups

Alembic runs before the Docker API starts. The first migration adopts matching legacy core tables without dropping them; the second adds management tables and the third enforces unique physical seats within each exam. Incompatible legacy schemas stop migration. Destructive downgrades are disabled.

Argon2 protects passwords. Session cookies are HttpOnly, expire after eight hours and use SameSite=Lax. Signed-by-session CSRF tokens protect authenticated mutations; allowed Origin checks add another boundary. Redis shares login limits across API processes. PostgreSQL advisory transaction locks serialize data mutations; direct SQLite use uses immediate transactions. Audit history covers successful operations. Local HTTP uses `COOKIE_SECURE=false`; use secure cookies and HTTPS if adapting this outside localhost. No public deployment is configured.

Back up Docker PostgreSQL:

```powershell
docker compose run --rm backup
```

Dated PostgreSQL custom-format dumps appear in `backups/`, which is git-ignored. Keep an additional copy outside this computer. Restore through PostgreSQL `pg_restore` into a separate empty database first; do not blindly overwrite your running database.

Back up the earlier local SQLite file:

```powershell
.\.venv\Scripts\python.exe -m backend.backup
```

Backups contain private records and password hashes. Restrict access. A pre-migration SQLite backup has been created for this expansion.

## Non-Docker development

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-ai.txt
.\.venv\Scripts\python.exe -m alembic -c backend/alembic.ini upgrade head
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --env-file .env
```

In another terminal: `cd frontend`, `npm.cmd install`, then `npm.cmd run dev`. Open http://127.0.0.1:5173. Default local storage is SQLite. Redis limits are enabled in Docker; the single-process local development fallback uses in-memory limits. Default automatic table creation is only a local convenience; Docker disables it and runs Alembic.

## Deferred validation and limitations

At the user's request, no classroom/camera acceptance testing or functional regression suite was run for v0.4. TypeScript production build, Python syntax, migration application, Compose validation and container startup checks are recorded separately. Earlier test results do not certify these expanded features. Existing tests need CSRF-aware clients before rerunning because session protection changed.

Not included: guaranteed reconstruction from arbitrary moving footage, a native mobile app, email-based self-service recovery, medical/accessibility detail collection, automatic invigilation or guarantees against copying. Groq activation requires your API key. Archive preserves history instead of permanently deleting records.


### Improving small/rear desk detection

The photo panel now offers furniture prompts, a 10-60% detection threshold (20% initially), and an enhanced three-pass scan. Enhanced mode detects on the whole image and two enlarged rear-room crops. Geometric suppression reduces repeated tabletop/whole-desk predictions and weak boxes covering groups. It does not prove the count is correct. Yellow dashed boxes have a model score below 30% and require extra review. Occluded desks, teacher furniture and unusual lab tables may still be missed or incorrectly included. Compare with the original image and correct suggestions before saving.

Cached vision weights load locally without a network metadata check. CPU inference defaults to four threads; VISION_CPU_THREADS can override it between one and eight. Image processing may take tens of seconds on a CPU, especially in enhanced mode.
