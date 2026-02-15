import os
import secrets
from typing import Optional
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import redis.asyncio as redis
from cryptography.fernet import Fernet
import uuid
import json
import base64
import hashlib
import asyncio
from datetime import datetime, timedelta
from urllib.parse import urlencode
import requests
from starlette.responses import Response
import jwt

# --- ADD LOGGER ---
import logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.getenv("FRONTEND_URL", "http://localhost:3000")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- CONFIG ---
KEYCLOAK_INTERNAL_URL = os.getenv("KEYCLOAK_INTERNAL_URL", "http://keycloak:8080")
KEYCLOAK_EXTERNAL_URL = os.getenv("KEYCLOAK_EXTERNAL_URL", "http://localhost:8080")
REALM = os.getenv("KEYCLOAK_REALM", "reports-realm")
CLIENT_ID = os.getenv("CLIENT_ID", "reports-frontend")
CLIENT_SECRET = os.getenv("CLIENT_SECRET", "")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")
KEYCLOAK_JWKS_URL = f"{KEYCLOAK_INTERNAL_URL}/realms/{REALM}/protocol/openid-connect/certs"

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

# --- INIT ---
redis_client = None
fernet = Fernet(Fernet.generate_key())  # In production, use secret from env

@app.on_event("startup")
async def startup():
    global redis_client
    redis_client = redis.from_url(REDIS_URL, decode_responses=False)

@app.on_event("shutdown")
async def shutdown():
    await redis_client.close()

# --- HELPERS ---
def generate_pkce_pair():
    code_verifier = base64.urlsafe_b64encode(os.urandom(32)).decode('utf-8').rstrip("=")
    code_challenge = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest()).decode('utf-8').rstrip("=")
    return code_verifier, code_challenge

def encrypt(data: str) -> bytes:  # <-- ИСПРАВЛЕНО
    return fernet.encrypt(data.encode())

def decrypt(token_bytes: bytes) -> str:
    return fernet.decrypt(token_bytes).decode()

# --- SESSION MANAGEMENT ---
SESSION_COOKIE_NAME = "session_id"
SESSION_EXPIRE_SECONDS = 3600 * 24  # 24 hours

async def create_session(user_data: dict):  # <-- ИСПРАВЛЕНО
    session_id = str(uuid.uuid4())
    user_data['created_at'] = datetime.utcnow().isoformat()
    user_data['updated_at'] = datetime.utcnow().isoformat()
    await redis_client.setex(f"session:{session_id}", SESSION_EXPIRE_SECONDS, json.dumps(user_data))
    return session_id

async def get_session(session_id: str) -> Optional[dict]:
    raw = await redis_client.get(f"session:{session_id}")
    if raw:
        return json.loads(raw.decode())
    return None

async def update_session(session_id: str, new_data: dict):
    existing = await get_session(session_id)
    if not existing:
        return False
    existing.update(new_data)
    existing['updated_at'] = datetime.utcnow().isoformat()
    await redis_client.setex(f"session:{session_id}", SESSION_EXPIRE_SECONDS, json.dumps(existing))
    return True

async def rotate_session(old_session_id: str) -> str:
    old_data = await get_session(old_session_id)
    if not old_data:
        raise HTTPException(status_code=401, detail="Invalid session")
    
    new_session_id = str(uuid.uuid4())
    await redis_client.delete(f"session:{old_session_id}")
    await redis_client.setex(f"session:{new_session_id}", SESSION_EXPIRE_SECONDS, json.dumps(old_data))
    return new_session_id

# --- ENDPOINTS ---

@app.get("/login")
async def login_start(request: Request):
    code_verifier, code_challenge = generate_pkce_pair()
    state = secrets.token_urlsafe(32)

    await redis_client.setex(f"oauth_state:{state}", 300, code_verifier)

    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": f"{str(request.base_url)}callback",  # Callback всегда на том же хосте
        "scope": "openid profile email",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256"
    }

    # Используем внешний URL для редиректа в браузере
    redirect_url = f"{KEYCLOAK_EXTERNAL_URL}/realms/{REALM}/protocol/openid-connect/auth?{urlencode(params)}"
    return RedirectResponse(url=redirect_url)

@app.get("/callback")
async def callback(code: str, state: str, request: Request):
    print("=== CALLBACK CALLED ===")  # <-- Добавьте
    logger.info(f"Callback called with code: {code[:10]}..., state: {state[:10]}...")

    stored_verifier_raw = await redis_client.get(f"oauth_state:{state}")
    if not stored_verifier_raw:
        logger.error(f"State not found in Redis: {state}")
        raise HTTPException(status_code=400, detail="Invalid or expired state")

    await redis_client.delete(f"oauth_state:{state}")

    logger.info(f"Using internal URL: {KEYCLOAK_INTERNAL_URL}")
    token_url = f"{KEYCLOAK_INTERNAL_URL}/realms/{REALM}/protocol/openid-connect/token"
    logger.info(f"Requesting token from: {token_url}")

    payload = {
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "code": code,
        "redirect_uri": f"{str(request.base_url)}callback",
        "code_verifier": stored_verifier_raw.decode()
    }

    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    try:
        resp = requests.post(token_url, data=payload, headers=headers, timeout=10)
    except requests.exceptions.ConnectionError as e:
        logger.error(f"Failed to connect to Keycloak: {e}")
        raise HTTPException(status_code=500, detail="Keycloak is not reachable")

    if resp.status_code != 200:
        logger.error(f"Token exchange failed: {resp.status_code}, {resp.text}")
        raise HTTPException(status_code=resp.status_code, detail="Token exchange failed")

    tokens = resp.json()
    logger.info("Tokens received successfully")

    session_id = await create_session({
        "access_token": tokens["access_token"],
        "refresh_token": encrypt(tokens["refresh_token"]).decode(),
        "expires_in": tokens["expires_in"],
        "refresh_expires_in": tokens.get("refresh_expires_in", 30 * 24 * 3600),
        "token_type": tokens["token_type"]
    })

    response = RedirectResponse(url=FRONTEND_URL)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_id,
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=SESSION_EXPIRE_SECONDS
    )
    return response

@app.get("/protected")
async def protected_endpoint(request: Request):
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if not session_id:
        raise HTTPException(status_code=401, detail="No session cookie")

    session_data = await get_session(session_id)
    if not session_data:  # <-- ИСПРАВЛЕНО
        raise HTTPException(status_code=401, detail="Session not found")

    # Rotate session ID for security
    new_session_id = await rotate_session(session_id)

    # Check if access_token is expired
    created_at = datetime.fromisoformat(session_data['created_at'])
    expires_at = created_at + timedelta(seconds=session_data['expires_in'])
    if datetime.utcnow() >= expires_at:
        # Refresh access token
        encrypted_refresh = session_data['refresh_token'].encode()
        refresh_token = decrypt(encrypted_refresh)

        # Используем внутренний URL для обновления токена
        refresh_url = f"{KEYCLOAK_INTERNAL_URL}/realms/{REALM}/protocol/openid-connect/token"
        payload = {
            "grant_type": "refresh_token",
            "client_id": CLIENT_ID,
            "refresh_token": refresh_token
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}

        resp = requests.post(refresh_url, data=payload, headers=headers)
        if resp.status_code != 200:
            # Refresh failed, invalidate session
            await redis_client.delete(f"session:{new_session_id}")
            raise HTTPException(status_code=401, detail="Could not refresh access token")

        new_tokens = resp.json()
        session_data["access_token"] = new_tokens["access_token"]
        session_data["refresh_token"] = encrypt(new_tokens["refresh_token"]).decode()
        session_data["expires_in"] = new_tokens["expires_in"]
        await update_session(new_session_id, session_data)

    response = JSONResponse({"message": "Access granted", "session_id": new_session_id})
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=new_session_id,
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=SESSION_EXPIRE_SECONDS
    )
    return response

@app.get("/reports")
async def get_reports(request: Request):
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if not session_id:
        raise HTTPException(status_code=401, detail="No session cookie")

    session_data = await get_session(session_id)
    if not session_data:
        raise HTTPException(status_code=401, detail="Session not found")

    # Rotate session ID for security
    new_session_id = await rotate_session(session_id)

    # Check if access_token is expired
    created_at = datetime.fromisoformat(session_data['created_at'])
    expires_at = created_at + timedelta(seconds=session_data['expires_in'])
    if datetime.utcnow() >= expires_at:
        # Refresh access token
        encrypted_refresh = session_data['refresh_token'].encode()
        refresh_token = decrypt(encrypted_refresh)

        refresh_url = f"{KEYCLOAK_INTERNAL_URL}/realms/{REALM}/protocol/openid-connect/token"
        payload = {
            "grant_type": "refresh_token",
            "client_id": CLIENT_ID,
            "refresh_token": refresh_token
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}

        resp = requests.post(refresh_url, data=payload, headers=headers)
        if resp.status_code != 200:
            await redis_client.delete(f"session:{new_session_id}")
            raise HTTPException(status_code=401, detail="Could not refresh access token")

        new_tokens = resp.json()
        session_data["access_token"] = new_tokens["access_token"]
        session_data["refresh_token"] = encrypt(new_tokens["refresh_token"]).decode()
        session_data["expires_in"] = new_tokens["expires_in"]
        await update_session(new_session_id, session_data)

    # Декодируем токен, чтобы получить email
    try:
        decoded_token = decode_jwt_token(session_data["access_token"])
        email = decoded_token.get("email")
        if not email:
            raise HTTPException(status_code=401, detail="Email not found in token")
    except Exception as e:
        logger.error(f"Could not decode JWT: {e}")
        raise HTTPException(status_code=401, detail="Invalid token")

    # Forward request to reports-api
    api_url = os.getenv("REPORTS_API_URL", "http://reports-api:8000/reports")
    headers = {
        "Authorization": f"Bearer {session_data['access_token']}",
        "Accept": "application/json",
        "X-User-Email": email
    }

    try:
        api_resp = requests.get(api_url, headers=headers)
        if api_resp.status_code != 200:
            raise HTTPException(status_code=api_resp.status_code, detail="API request failed")

        # Вместо JSON-данных возвращаем URL к CDN
        response_data = api_resp.json()
        cdn_url = response_data.get("cdn_url")

        if not cdn_url:
            raise HTTPException(status_code=500, detail="CDN URL not provided")

        # Возвращаем URL для скачивания
        response = JSONResponse({"download_url": cdn_url})
    except requests.exceptions.RequestException:
        raise HTTPException(status_code=500, detail="Failed to fetch reports from API")

    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=new_session_id,
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=SESSION_EXPIRE_SECONDS
    )
    return response

def get_public_key(jwt_token: str):
    header = jwt.get_unverified_header(jwt_token)
    kid = header.get("kid")
    resp = requests.get(KEYCLOAK_JWKS_URL)
    jwks = resp.json()
    for jwk in jwks['keys']:
        if jwk['kid'] == kid:
            from jwt.algorithms import RSAAlgorithm
            return RSAAlgorithm.from_jwk(json.dumps(jwk))
    return None

def decode_jwt_token(jwt_token: str):
    public_key = get_public_key(jwt_token)
    return jwt.decode(jwt_token, public_key, algorithms=["RS256"])

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)