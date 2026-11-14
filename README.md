# KIT Voice Assistant

An AI-powered, real-time voice assistant for **Knotopian Institute of Technology (KIT)**. It helps prospective students with admissions and gives authenticated students and teachers secure access to college services.

## Highlights

- Voice conversation with Groq Whisper speech-to-text and Groq Orpheus speech replies.
- Public admissions assistant for courses, fees, eligibility, dates, documents, and applications.
- Student services for attendance, timetable, rules, leave, on-duty requests, placements, and lesson doubts.
- Teacher request-review and approval support.
- Role-based access control, encrypted session cookies, confirmation before write actions, and idempotent submissions.
- Private conversation history and searchable handbook, brochure, notes, and circulars.
- Responsive light-first UI, dark mode, original campus visuals, and selectable voices.

## Architecture

```text
Browser
  ├─ MediaRecorder + Web Audio VAD
  └─ HTML / CSS / JavaScript UI
             │
             ▼
FastAPI backend
  ├─ Authentication, encrypted sessions, RBAC, rate limiting
  ├─ Groq Whisper ──► transcript
  ├─ AI agent + OpenAPI-generated tools ──► KIT College API
  ├─ BM25 retrieval ◄── college PDFs
  ├─ Groq Orpheus ──► spoken reply
  └─ SQLite ──► sessions and conversation history
```

## Technology stack

| Layer | Technology | Purpose |
|---|---|---|
| Frontend | HTML, CSS, JavaScript | Responsive interface, theme, forms, history, and voice controls |
| Audio capture | MediaRecorder, Web Audio API | Browser microphone recording and end-of-speech detection |
| Backend | Python, FastAPI, Uvicorn | APIs, authentication, streaming, validation, and security controls |
| AI reasoning | Groq GPT-OSS | Intent handling, tool selection, and response generation |
| Speech-to-text | Groq Whisper | Converts recorded speech into text |
| Text-to-speech | Groq Orpheus | Generates selectable assistant voices |
| Retrieval | PyPDF + BM25 | Answers questions from college documents |
| College integration | REST API + OpenAPI | Live college data and runtime tool generation |
| Storage | SQLite | Conversations and session data |
| Security | Fernet, HttpOnly cookies, RBAC | Encrypted sessions and role-specific access |
| Deployment | Docker, Docker Compose, Vercel | Consistent local, container, and cloud deployment |

## Roles and access

| Role | Access |
|---|---|
| Visitor | College details, courses, admissions information, placement-drive overview, admission application |
| Student | Their own attendance, timetable, rules, leave/on-duty requests, and placement registration |
| Teacher | Their own services and teacher approval queue |

All write actions require the assistant to collect details, read them back, and receive confirmation before submitting. Idempotency keys prevent duplicate submissions.

## Project structure

```text
kit/
  agent.py        AI orchestration
  auth.py         login and encrypted session handling
  college.py      college API client
  config.py       environment configuration
  db.py           SQLite persistence
  knowledge.py    PDF retrieval and BM25 search
  main.py         FastAPI routes
  tools.py        OpenAPI tool generation and RBAC
  voice.py        Groq STT and TTS
static/           frontend pages, styles, scripts, and images
tests/            offline API, security, tool, and retrieval tests
api/index.py      Vercel Python entrypoint
Dockerfile        production container image
docker-compose.yml local container setup
vercel.json       Vercel configuration
```

## Run locally

### Prerequisites

- Python 3.11 or 3.12
- Groq API key
- KIT College API key
- Docker Desktop (optional)

### Windows PowerShell

```powershell
Copy-Item .env.example .env
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn kit.main:app --reload
```

Open [http://localhost:8000](http://localhost:8000).

### Environment variables

```env
COLLEGE_API_URL=https://ht.knowvally.com
COLLEGE_API_KEY=your_college_key
GROQ_API_KEY=your_groq_key
SESSION_SECRET=your_long_random_secret
TIMEZONE=Asia/Kolkata
```

Never commit `.env` or expose API keys in client-side code.

## Docker

Build and start the app with Docker Compose:

```bash
docker compose up --build
```

Or build and run manually:

```bash
docker build -t kit-voice-assistant .
docker run --env-file .env -p 8000:8000 kit-voice-assistant
```

## Tests

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest
```

Tests cover authentication, encrypted sessions, RBAC, tool generation, confirmation and idempotency, history isolation, document retrieval, voice helpers, AI fallback, and security headers.

## Deployment

The project includes Vercel and Docker deployment configuration.

For Vercel, set these Production environment variables in Project Settings:

```text
COLLEGE_API_URL
COLLEGE_API_KEY
GROQ_API_KEY
SESSION_SECRET
TIMEZONE
```

Deploy with:

```bash
npx vercel --prod
```

HTTPS is required for microphone access. For durable production history, use a persistent disk with the Docker deployment or connect the storage layer to a managed database.
