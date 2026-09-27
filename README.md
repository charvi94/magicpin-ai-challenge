# Vera AI — magicpin AI Challenge

Deterministic, stateful HTTP bot for the magicpin AI Challenge.

## Run locally

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8080
```

Required endpoints:
- POST /v1/context
- POST /v1/tick
- POST /v1/reply
- GET /v1/healthz
- GET /v1/metadata
