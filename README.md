# Self-hosted Meetings

This is a conversion of the original **Next.js-only LiveKit Meet** app. Token minting and recording no longer live in Next.js API routes or LiveKit Cloud. They run on a **Django API**, with **LiveKit Server in Docker** on your machine.

| Original | This project |
| --- | --- |
| Next.js pages + Next.js `/api/*` | Next.js UI only |
| `livekit-server-sdk` in Node | `livekit-api` in Django |
| LiveKit Cloud (`wss://*.livekit.cloud`) | `livekit/livekit-server` container (`ws://localhost:7880`) |

## Architecture

```
Browser (http://localhost)
        |
        v
     Caddy :80
     |          \
     | /         \ /api/*
     v            v
 Next.js :3000   Django :8000
                      |
                      | issues JWT
                      v
              LiveKit :7880  <-- browser also connects here (WebRTC)
                      |
                    Redis
Django also uses Postgres for Django's own tables.
```

Join flow:

1. **Start meeting** (or save a schedule) creates the meeting in Django and stores a host key in that browser. The shareable room URL does not include the key, so opening the link does not make someone the host.
2. The host's PreJoin goes straight in. Everyone else stays on PreJoin. The button reads **Waiting for the host to accept you** until a host or co-host clicks **Accept**. **Decline** shows **The host has declined your request** and the button returns to **Join meeting**.
3. Accept issues a LiveKit JWT. The host token has `roomAdmin`. Co-hosts do not: they admit, mute, and remove through Django, and only the host can assign co-hosts or end the meeting.
4. The browser connects with `Room.connect(serverUrl, participantToken)` to the self-hosted SFU at `ws://localhost:7880`.

`POST /api/getToken` no longer mints a token. A direct token would skip the waiting room.

Env vars match the [Python / TokenSource docs](https://docs.livekit.io/frontends/reference/tokens-grants.md): `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`. `LIVEKIT_API_URL` is extra for Docker (Django talks to LiveKit over HTTP on the compose network).

```
meetings/
  docker-compose.yml          # one-command self-host stack
  .env.example                # secrets and public LiveKit URL
  infra/
    Caddyfile                 # reverse proxy: UI + Django /api
    livekit.yaml              # LiveKit keys, Redis, RTC ports
  backend/                    # Django project
    Dockerfile
    manage.py
    config/                   # settings, urls, wsgi (project package)
    apps/meetings/            # API app
      views.py                # schedules and slides
      views_recordings.py     # StartEgress, catalog, webhook
      serializers.py          # TokenSource request/response
      services/tokens.py      # livekit.api.AccessToken + VideoGrants
      services/egress.py      # StartEgress TemplateSource → MP4
  frontend/                   # Next.js App Router UI (no /app/api)
```

Why this layout:

- **`config/`** is the Django project so it is not named `django` or `meetings` (those collide with the app and the library).
- **`apps/meetings/`** is the only domain app. Token logic sits in `services/` so views stay thin.
- **`frontend/`** is a separate Node image. Next.js never sees `LIVEKIT_API_SECRET`.
- **`infra/`** holds LiveKit and Caddy config, not application code.
- **Caddy on port 80** keeps the browser same-origin (`/api` and `/`), so cookies and CORS stay simple.

## LiveKit docs mapping

| Piece | LiveKit reference | This repo |
| --- | --- | --- |
| Access tokens / grants | [Tokens & grants](https://docs.livekit.io/frontends/reference/tokens-grants.md) | `backend/apps/meetings/services/tokens.py` (`livekit.api.AccessToken`, `VideoGrants`) |
| Production token HTTP API | [Token endpoints](https://docs.livekit.io/frontends/build/authentication/endpoint.md) | `POST /api/meetings/<room>/join` after the host admits you |
| Frontend fetch | `TokenSource.endpoint` in [livekit-client](https://docs.livekit.io/frontends/build/authentication/endpoint.md) | `frontend/lib/livekitToken.ts` |
| Client connect | `Room.connect(serverUrl, participantToken)` | `PageClientImpl` + `@livekit/components-react` `VideoConference` |
| Self-hosted SFU | [Self-hosting](https://docs.livekit.io/transport/self-hosting.md) / [local](https://docs.livekit.io/transport/self-hosting/local.md) | `infra/livekit.yaml` + `livekit/livekit-server` |
| Reverse proxy + Docker | [VM / Docker Compose](https://docs.livekit.io/transport/self-hosting/vm.md) | `docker-compose.yml` + Caddy |
| Egress | [Egress](https://docs.livekit.io/transport/media/ingress-egress/egress/) / [API](https://docs.livekit.io/reference/other/egress/api/) | `StartEgress` + `TemplateSource` in `services/egress.py` |
| Env names | `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | `.env.example`, `backend/.env.example`, `frontend/.env.example` |

`LIVEKIT_API_URL` is the only extra variable: inside Docker, Django must reach LiveKit at `http://livekit:7880` while the **browser** must use `LIVEKIT_URL=ws://localhost:7880`.

## Prerequisites

- Docker Desktop
- Ports **80**, **7880**, **7881**, **7882/udp** free on the host

## Run (Docker)

```powershell
cd C:\Users\Nico\meetings
copy .env.example .env
docker compose up --build
```

Open **http://localhost**.

PowerPoint upload is converted by LibreOffice in the backend image, or by the converter image when Django runs on the host. Each run keeps its typeface when that face, or a metric-compatible one, is installed. Otherwise text under 30pt uses Calibri, text from 30pt through 40pt uses Anton, and text above 40pt uses Bodoni. Text above 40pt is also drawn 8pt smaller. Both images install those faces (Carlito stands in for Calibri, and Libre Bodoni for Bodoni), plus the Microsoft core fonts and the other Google Slides families. `docker compose up --build` picks up those fonts. Google Sans is not redistributable, so that name uses Inter.

Use **Start Meeting**, enter a name, allow camera/mic. Open the same room URL in a second browser to verify two participants.

API check: http://localhost/api/health

Token check (TokenSource schema):

```powershell
$created = Invoke-RestMethod -Method Post -Uri http://localhost/api/meetings -ContentType "application/json" -Body '{"room_name":"demo-room"}'
Invoke-RestMethod -Method Post -Uri http://localhost/api/meetings/demo-room/join -ContentType "application/json" -Headers @{ "X-Host-Key" = $created.host_key } -Body '{"participant_name":"nico"}'
```

If LiveKit keys in `.env` change, update `infra/livekit.yaml` `keys:` to match (`LIVEKIT_API_KEY: LIVEKIT_API_SECRET`).

## Local development without rebuilding images

Use three env files. They are not copies of each other.

| File | Who reads it | What belongs in it |
| --- | --- | --- |
| `.env` | Docker Compose, from `.env.example` | Shared secrets. Compose never reads `backend/.env`. |
| `backend/.env` | `python manage.py runserver`, from `backend/.env.example` | Host addresses only. Secrets come from the root file. |
| `frontend/.env.local` | `npm run dev`, from `frontend/.env.example` | Browser settings. No secrets. |

Django loads `backend/.env` first, then fills anything still missing from the repo-root `.env`. A variable Compose already set is left alone, so `backend/.env` does not change the container. Service URLs such as `http://livekit:7880` and `http://minio:9000` are defaults in `docker-compose.yml`, so they are not repeated in the root example. `NEXT_PUBLIC_*` values are compiled into the browser.

Terminal 1 — LiveKit, Redis, Postgres, MinIO, Egress, and the PowerPoint converter:

```powershell
docker compose up redis db livekit converter minio minio-init egress
```

The converter is Gotenberg with the slide fonts above. Local Django sends `.pptx` files to it and keeps the speaker notes itself.

Terminal 2 — Django:

```powershell
cd backend
copy .env.example .env
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver 8000
```

`S3_ENDPOINT` in that file is `http://host.docker.internal:9000` because Egress is still in Docker and has to upload through the host. `S3_PUBLIC_ENDPOINT` is `http://127.0.0.1:9000` because that is the URL the browser opens. Restart runserver after changing `.env`.

Terminal 3 — Next.js:

```powershell
cd frontend
copy .env.example .env.local
npm install
npm run dev
```

Open http://localhost:3000. Next rewrites `/api/*` to Django via `API_INTERNAL_URL`.

## LAN access

On another device, `127.0.0.1` is the *other* machine. Set:

- `.env` `LIVEKIT_URL=ws://YOUR_LAN_IP:7880`
- `infra/livekit.yaml` `rtc.node_ip: YOUR_LAN_IP`
- Recreate LiveKit: `docker compose up -d --force-recreate livekit backend`

## Recordings

The host or a co-host starts and stops recording from the in-call settings menu. Django calls LiveKit [`StartEgress`](https://docs.livekit.io/reference/other/egress/api/) with a `TemplateSource` (the built-in speaker layout) and one MP4 output. That replaces the deprecated room-composite API. The file is `{room_name}/{time}.mp4` in MinIO. Request-level `StorageConfig` is what Egress uploads with; `infra/egress.yaml` is only the fallback.

LiveKit server `v1.13.7` is required for `StartEgress`. The Egress container (`livekit/egress:v1.14.1`) shares Redis with LiveKit, so extra copies of that service take more rooms without changing Django. Chrome needs `SYS_ADMIN` and a larger `/dev/shm`.

`ListEgress` does not keep finished jobs. LiveKit posts `egress_ended` to `/api/livekit/webhook`, and Django stores the object key on the meeting. People who were admitted can list `/api/recordings` and download a file. When `S3_PUBLIC_ENDPOINT` is set, the download is a presigned URL so the video does not pass through Django.

Encrypted meetings are not recorded. Egress joins as its own participant and subscribes only to the tracks it needs; this app does not change those subscriptions.

## Notes vs the original repo

- Cloud region URLs (`*.livekit.cloud`) are unused. Django returns `LIVEKIT_URL` as TokenSource `server_url`.
- Krisp noise filter is Cloud-oriented; it is left off by default.
- Original Cloud API keys in `Downloads\meetings\.env.local` are **not** copied here.

## Troubleshooting

- **Blank video / cannot connect:** confirm `docker compose ps` shows `livekit` healthy and the browser uses `ws://localhost:7880`. UDP 7882 must be published.
- **401 / invalid token:** `.env` keys do not match `infra/livekit.yaml`.
- **Port 80 in use:** change Caddy ports in `docker-compose.yml` to `"8080:80"` and open http://localhost:8080.
