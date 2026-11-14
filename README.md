# KIT Voice Assistant

A real-time voice assistant for **Knotopian Institute of Technology (KIT)**. Visitors ask about admissions and can apply by voice. Students and teachers log in and get personal services (attendance, rules, leave, on-duty, approvals), only for themselves. Every conversation is saved in a History page.

## Requirements checklist

| Requirement | How it is done |
|---|---|
| Voice conversation | Mic with automatic end-of-speech detection, hands-free mode, tap-to-interrupt. Speech-to-text is Whisper on Groq (not the browser's Web Speech API). Replies stream back sentence by sentence so speech starts early. |
| Public home page | College name, intro and courses from the college API, plus a "Talk to us" assistant. It can only use the six visitor calls and cannot see any student data. |
| Admission application | The assistant collects the required fields from the API's own schema, reads them back, and submits after a spoken "yes". |
| Login (students, teachers) | Server-side session. The college token is never sent to the browser (httpOnly cookie holds only a random session id). |
| Leave and on-duty requests | Same confirm-then-submit flow. Handbook rules and exam dates are checked first. |
| Teacher approvals | Teachers get the approval endpoints; students are blocked from them in code. |
| History page | `/history` lists and reopens past conversations, with delete. History is private to each person. |
| Placement registration, lesson doubts | Placement endpoints are tools. Lesson notes are searched like the handbook. |
| Keeps working if one AI service fails | Chat falls back across models, Whisper across two models, voice falls back to the browser's voice, and if all chat models are down it answers straight from the documents. |

## Architecture

```
Browser (mic + VAD) --audio--> /api/voice --> Whisper STT --> agent --> Groq LLM <--> tools --> College API
                                                                |                       (tools are generated
              <-- streamed events: transcript, status, reply, audio clips --            from its OpenAPI spec)
Documents (handbook, brochure, notes) --> chunks --> BM25 search --> injected into the prompt
SQLite: sessions, conversations, messages
```

## Tech stack

- **Frontend:** semantic HTML, modern CSS (responsive light/dark/system themes), and vanilla JavaScript with `MediaRecorder`, Web Audio voice-activity detection, and streamed NDJSON responses.
- **Backend:** Python 3.11+/FastAPI, Uvicorn, Pydantic, HTTPX, and SQLite.
- **AI voice:** Groq Whisper (`whisper-large-v3-turbo` with fallback) for speech-to-text; Groq Orpheus for server-generated speech, with browser speech synthesis only as a TTS fallback.
- **AI orchestration:** Groq OpenAI-compatible tool calling with model fallback; runtime tools are generated from the college API's OpenAPI specification.
- **Knowledge:** PyPDF extraction plus a dependency-free BM25 retrieval index for the handbook, admissions brochure, notes, and circulars.
- **Deployment:** Docker/Render or Vercel Python serverless; environment-based secrets and HTTPS microphone support.

Key design choices:

- **Tools come from the college API's OpenAPI spec**, read at runtime and refreshed every 10 minutes. If endpoints or fields change during the event, the assistant adapts without a code change. If the spec can't be downloaded, a small fallback tool set takes over.
- **Safety is enforced in code, not only in the prompt** (`kit/tools.py`): visitors get an allow-list of public calls; students can't reach staff, approval or other students' endpoints; every write needs `user_confirmed=true`; writes carry an `Idempotency-Key` so retries never create duplicates.
- **Prompt-injection aware**: document text and API results are treated as data, never as instructions.
- **Security**: strict CSP (no inline scripts), same-origin check on writes, rate limiting, size limits, httpOnly cookies, per-owner history isolation, secrets only in environment variables.
- **Retrieval**: BM25 keyword ranking over the college PDFs. It needs no model download and runs on any free host. Public visitors only search admission documents.

## Run locally

Windows PowerShell (Python 3.11 or 3.12 is required):

```powershell
Copy-Item .env.example .env   # skip this if your private .env already exists
# Fill in COLLEGE_API_KEY and GROQ_API_KEY in .env; do not commit it.
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn kit.main:app --reload
```

macOS/Linux:

```bash
cp .env.example .env
python3 -m venv .venv && source .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn kit.main:app --reload
```

Open http://localhost:8000. Or with Docker: `docker compose up --build`. The app now reads the ignored `.env` file automatically in local development; on a host, set the same values as environment variables instead.

Check http://localhost:8000/api/health: `tools_source` should be `openapi` and `documents_indexed` should list the handbook and brochure.

Demo logins (password `Kit@2026`): students `arjun`, `priya`, `kavya`, ...; teachers `lakshmi`, `senthil`.

## Deploy (HTTPS is required for the microphone)

### Vercel

This repository includes `vercel.json` and `api/index.py`, so import the repository into Vercel as-is.

1. Push this folder to a GitHub repository (confirm `.env` is not included).
2. In Vercel, select **Add New → Project → Import** and choose that repository. Leave the framework preset as **Other**.
3. In **Project Settings → Environment Variables**, add `COLLEGE_API_URL`, `COLLEGE_API_KEY`, `GROQ_API_KEY`, and `TIMEZONE=Asia/Kolkata`. Select both **Production** and **Preview** for each.
4. Click **Deploy**, open `/api/health`, then test a typed message and microphone reply over the generated HTTPS URL.

Never add the `.env` file or either key to GitHub. If using the CLI after `npx vercel login`, run `npx vercel --prod` from the project folder; set the environment variables in the dashboard first.

Vercel is suitable for the demo, but its Python filesystem is ephemeral. For conversation history that must survive cold starts and redeploys, deploy the included Docker configuration to Render with a persistent disk mounted at `/data`, or replace the SQLite adapter with a managed database before production.

Render (free): New > Blueprint > pick this repo (`render.yaml`), then set `COLLEGE_API_KEY` and `GROQ_API_KEY` in the dashboard. Any host that runs a Dockerfile works the same way (Railway, Fly.io, Koyeb). The app reads the `PORT` variable.

The free tier sleeps when idle and has no persistent disk, so open the site once before judging. History persists while the container lives; attach a disk at `/data` to keep it across deploys.

## Tests

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest
```

The tests run offline against an in-process fake college API. They cover tool generation from OpenAPI, role permissions, write confirmation and idempotency, document search, history privacy, login, the full chat-with-tool loop, the LLM-outage fallback, and security headers.

## Known limits

- Retrieval is keyword based, so very differently worded questions may match less well than with embeddings.
- Server voice (Orpheus) may need its terms accepted once in the Groq console. Until then the browser voice speaks the reply.
- Tested against a faithful fake of the college API, not guaranteed against every endpoint of the live one. Use `/api/health` and the History page to verify quickly.
