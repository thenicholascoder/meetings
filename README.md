# Meetings
## 1. Docker

From the repo root:

```powershell
copy .env.example .env
docker compose up redis db livekit minio minio-init egress
```

## 2. Backend

```powershell
cd backend
copy .env.example .env
```

`backend/.env` sends Chrome to the dev server:

```
RECORDING_TEMPLATE_URL=http://host.docker.internal:3000/egress
```

Leave that line in place. `http://caddy/egress` does not resolve from Egress unless the backend container is running, and the recording fails with `net::ERR_NAME_NOT_RESOLVED`.

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver 8000
```

## 3. Frontend

```powershell
cd frontend
copy .env.example .env.local
npm install
npm run dev
```

Open http://localhost:3000
