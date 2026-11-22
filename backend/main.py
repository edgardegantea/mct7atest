import os
import sqlite3
import secrets
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel

app = FastAPI()
ADMIN_KEY = os.environ.get("API_KEY", "")
BASE_DIR = os.environ.get("BASE_DIR", "/var/www/maewalliscorp.org/test/api/mct7atest" if os.path.exists("/var/www/maewalliscorp.org/test/api/mct7atest") else os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get("DB_PATH", f"{BASE_DIR}/iot.db")
COOKIE_NAME = "iot_session"

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS usuarios (
            username TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sesiones (
            token_hash TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            expira_en TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS codigos_emparejamiento (
            codigo TEXT PRIMARY KEY,
            expira_en TEXT NOT NULL,
            usado INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS dispositivos (
            mac TEXT PRIMARY KEY,
            nombre TEXT NOT NULL,
            token_hash TEXT NOT NULL,
            activo INTEGER DEFAULT 1,
            creado_en TEXT NOT NULL,
            led_deseado INTEGER DEFAULT 0,
            led_actual INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS lecturas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mac TEXT NOT NULL,
            temperatura REAL NOT NULL,
            wifi_rssi INTEGER NOT NULL,
            recibido_en TEXT NOT NULL,
            led_estado INTEGER DEFAULT 0
        );
        """)
        # Migraciones automáticas si las tablas ya existían previamente
        cols_disp = [c[1] for c in conn.execute("PRAGMA table_info(dispositivos)").fetchall()]
        if "led_deseado" not in cols_disp:
            conn.execute("ALTER TABLE dispositivos ADD COLUMN led_deseado INTEGER DEFAULT 0")
        if "led_actual" not in cols_disp:
            conn.execute("ALTER TABLE dispositivos ADD COLUMN led_actual INTEGER DEFAULT 0")

        cols_lec = [c[1] for c in conn.execute("PRAGMA table_info(lecturas)").fetchall()]
        if "led_estado" not in cols_lec:
            conn.execute("ALTER TABLE lecturas ADD COLUMN led_estado INTEGER DEFAULT 0")

init_db()

def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()

def hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000).hex()

def verificar_admin(request: Request, x_admin_key: str = "") -> str:
    # 1) Permite X-Admin-Key para scripts/curl
    if x_admin_key and ADMIN_KEY and secrets.compare_digest(x_admin_key, ADMIN_KEY):
        return "api-admin"
    # 2) Permite cookie de sesión del navegador
    cookie = request.cookies.get(COOKIE_NAME, "")
    if cookie:
        ahora = datetime.now(timezone.utc)
        with get_db() as conn:
            row = conn.execute(
                "SELECT username, expira_en FROM sesiones WHERE token_hash = ?",
                (hash_token(cookie),),
            ).fetchone()
        if row and datetime.fromisoformat(row["expira_en"]) > ahora:
            return row["username"]
    raise HTTPException(status_code=401, detail="No autorizado")

class LoginReq(BaseModel):
    username: str
    password: str

class ActivarReq(BaseModel):
    mac: str
    codigo: str
    nombre: str = "esp32-c3-supermini"

class LecturaReq(BaseModel):
    temperatura: float
    wifi_rssi: int
    led_estado: Optional[int] = None

class LedReq(BaseModel):
    estado: bool

@app.get("/")
def panel_web():
    return FileResponse(os.path.join(BASE_DIR, "dashboard.html"))

@app.post("/api/auth/login")
def login(req: LoginReq, response: Response):
    with get_db() as conn:
        user = conn.execute(
            "SELECT username, password_hash, salt FROM usuarios WHERE username = ?",
            (req.username.strip(),),
        ).fetchone()
        if not user or not secrets.compare_digest(hash_password(req.password, user["salt"]), user["password_hash"]):
            raise HTTPException(status_code=401, detail="Usuario o contraseña incorrectos")

        session_token = secrets.token_hex(32)
        expira = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
        conn.execute(
            "INSERT INTO sesiones (token_hash, username, expira_en) VALUES (?, ?, ?)",
            (hash_token(session_token), user["username"], expira),
        )
    response.set_cookie(
        key=COOKIE_NAME,
        value=session_token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=7 * 24 * 3600,
    )
    return {"ok": True, "username": user["username"]}

@app.post("/api/auth/logout")
def logout(request: Request, response: Response):
    cookie = request.cookies.get(COOKIE_NAME, "")
    if cookie:
        with get_db() as conn:
            conn.execute("DELETE FROM sesiones WHERE token_hash = ?", (hash_token(cookie),))
    response.delete_cookie(COOKIE_NAME)
    return {"ok": True}

@app.get("/api/auth/me")
def me(request: Request, x_admin_key: str = Header(default="")):
    username = verificar_admin(request, x_admin_key)
    return {"username": username}

@app.post("/api/admin/codigo")
def generar_codigo(request: Request, x_admin_key: str = Header(default="")):
    verificar_admin(request, x_admin_key)
    codigo = f"{secrets.randbelow(1000000):06d}"
    expira = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO codigos_emparejamiento (codigo, expira_en, usado) VALUES (?, ?, 0)",
            (codigo, expira),
        )
    print(f"[ADMIN] Nuevo código de emparejamiento: {codigo}", flush=True)
    return {"codigo": codigo, "expira_en_segundos": 300}

@app.get("/api/admin/codigo/{codigo}/estado")
def estado_codigo(codigo: str, request: Request, x_admin_key: str = Header(default="")):
    verificar_admin(request, x_admin_key)
    with get_db() as conn:
        row = conn.execute(
            "SELECT codigo, expira_en, usado FROM codigos_emparejamiento WHERE codigo = ?",
            (codigo.strip(),),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Código no encontrado")
    expirado = datetime.fromisoformat(row["expira_en"]) < datetime.now(timezone.utc)
    return {"codigo": row["codigo"], "usado": bool(row["usado"]), "expirado": expirado}

@app.post("/api/dispositivos/activar")
def activar_dispositivo(req: ActivarReq):
    ahora = datetime.now(timezone.utc)
    with get_db() as conn:
        row = conn.execute(
            "SELECT codigo, expira_en, usado FROM codigos_emparejamiento WHERE codigo = ?",
            (req.codigo.strip(),),
        ).fetchone()
        if not row or row["usado"] == 1:
            raise HTTPException(status_code=400, detail="Código inválido o ya usado")
        if datetime.fromisoformat(row["expira_en"]) < ahora:
            raise HTTPException(status_code=400, detail="Código expirado")

        device_token = secrets.token_hex(32)
        conn.execute("UPDATE codigos_emparejamiento SET usado = 1 WHERE codigo = ?", (req.codigo.strip(),))
        conn.execute(
            """INSERT INTO dispositivos (mac, nombre, token_hash, activo, creado_en, led_deseado, led_actual)
               VALUES (?, ?, ?, 1, ?, 0, 0)
               ON CONFLICT(mac) DO UPDATE SET nombre=excluded.nombre, token_hash=excluded.token_hash, activo=1""",
            (req.mac, req.nombre, hash_token(device_token), ahora.isoformat()),
        )
    print(f"[PROVISION] Dispositivo {req.mac} activado correctamente", flush=True)
    return {"ok": True, "mac": req.mac, "device_token": device_token}

@app.post("/api/lecturas")
def recibir_lectura(data: LecturaReq, x_device_token: str = Header(default="")):
    if not x_device_token:
        raise HTTPException(status_code=401, detail="Falta X-Device-Token")
    with get_db() as conn:
        dev = conn.execute(
            "SELECT mac, nombre, led_deseado FROM dispositivos WHERE token_hash = ? AND activo = 1",
            (hash_token(x_device_token),),
        ).fetchone()
        if not dev:
            raise HTTPException(status_code=401, detail="Token de dispositivo inválido o revocado")
        ahora = datetime.now(timezone.utc).isoformat()
        led_val = 1 if data.led_estado else 0 if data.led_estado is not None else None
        conn.execute(
            "INSERT INTO lecturas (mac, temperatura, wifi_rssi, recibido_en, led_estado) VALUES (?, ?, ?, ?, ?)",
            (dev["mac"], data.temperatura, data.wifi_rssi, ahora, led_val if led_val is not None else 0),
        )
        if led_val is not None:
            conn.execute(
                "UPDATE dispositivos SET led_actual = ? WHERE mac = ?",
                (led_val, dev["mac"]),
            )
        led_cmd = dev["led_deseado"] if dev["led_deseado"] is not None else 0
    print(f"[LECTURA] {dev['mac']} -> T={data.temperatura} RSSI={data.wifi_rssi} LED={data.led_estado} | CMD_DESEADO={led_cmd}", flush=True)
    return {"ok": True, "mac": dev["mac"], "led_deseado": int(led_cmd)}

@app.get("/api/admin/dispositivos")
def listar_dispositivos(request: Request, x_admin_key: str = Header(default="")):
    verificar_admin(request, x_admin_key)
    ahora = datetime.now(timezone.utc)
    with get_db() as conn:
        rows = conn.execute("""
            SELECT d.mac, d.nombre, d.activo, d.creado_en, d.led_deseado, d.led_actual,
                   (SELECT MAX(l.recibido_en) FROM lecturas l WHERE l.mac = d.mac) AS ultima_lectura
            FROM dispositivos d
            ORDER BY d.creado_en DESC
        """).fetchall()
    res = []
    for r in rows:
        item = dict(r)
        item["led_deseado"] = bool(item.get("led_deseado", 0))
        item["led_actual"] = bool(item.get("led_actual", 0))
        if item["ultima_lectura"]:
            delta = (ahora - datetime.fromisoformat(item["ultima_lectura"])).total_seconds()
            item["online"] = delta <= 30
        else:
            item["online"] = False
        res.append(item)
    return res

@app.post("/api/admin/dispositivos/{mac}/led")
def controlar_led(mac: str, req: LedReq, request: Request, x_admin_key: str = Header(default="")):
    verificar_admin(request, x_admin_key)
    nuevo_estado = 1 if req.estado else 0
    with get_db() as conn:
        cur = conn.execute(
            "UPDATE dispositivos SET led_deseado = ? WHERE mac = ? AND activo = 1",
            (nuevo_estado, mac),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Dispositivo no encontrado o inactivo")
    print(f"[CONTROL] Dispositivo {mac} -> LED deseado={nuevo_estado}", flush=True)
    return {"ok": True, "mac": mac, "led_deseado": nuevo_estado}

@app.post("/api/admin/dispositivos/{mac}/revocar")
def revocar_dispositivo(mac: str, request: Request, x_admin_key: str = Header(default="")):
    verificar_admin(request, x_admin_key)
    with get_db() as conn:
        conn.execute("UPDATE dispositivos SET activo = 0 WHERE mac = ?", (mac,))
    return {"ok": True, "mac": mac}

@app.get("/api/admin/lecturas")
def ver_lecturas(request: Request, limit: int = 20, x_admin_key: str = Header(default="")):
    verificar_admin(request, x_admin_key)
    with get_db() as conn:
        rows = conn.execute(
            "SELECT mac, temperatura, wifi_rssi, recibido_en, led_estado FROM lecturas ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]
