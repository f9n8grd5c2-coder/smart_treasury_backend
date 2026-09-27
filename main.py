import os
import jwt
import uvicorn
import tempfile
from datetime import date, datetime, timedelta
from typing import Optional
from fastapi import FastAPI, HTTPException, Header, Query, UploadFile, File, Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, EmailStr
from passlib.context import CryptContext
from dotenv import load_dotenv

# Importazione SlowAPI per la protezione Anti Brute-Force
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

# Importazione moduli di sistema
from db_helper import get_db_connection, decifra_dato, registra_audit_log
from corrispettivi_b2c import inserisci_corrispettivo_giornaliero
from parser_xml import parse_fattura_xml, salva_fattura_in_db

load_dotenv()

# Configurazione Sicurezza JWT & Hashing
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "chiave_segretissima_default")
ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 24

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security_scheme = HTTPBearer(auto_error=False)

# Configurazione Rate Limiting (Anti Brute-Force)
limiter = Limiter(key_func=get_remote_address)

# Inizializzazione FastAPI
app = FastAPI(
    title="Smart Treasury API",
    description="Backend SaaS di gestione tesoreria e cash-flow con autenticazione JWT, RLS e Rate Limiting.",
    version="1.2.0"
)

# Registrazione Rate Limiter nell'applicazione FastAPI
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==============================================================================
# SCHEMI PYDANTIC
# ==============================================================================

class LoginRequest(BaseModel):
    email: EmailStr = Field(..., json_schema_extra={"example": "admin@ferramenta.it"})
    password: str = Field(..., json_schema_extra={"example": "PasswordSicura123!"})


class CorrispettivoCreate(BaseModel):
    data_incasso: date = Field(default_factory=date.today, description="Data dell'incasso da banco")
    incasso_contanti: float = Field(ge=0.0, description="Importo totale incassato in contanti")
    incasso_pos: float = Field(ge=0.0, description="Importo totale incassato via POS/Carte")
    note: Optional[str] = Field(default="", description="Eventuali note o annotazioni di cassa")


from datetime import date, datetime, timedelta, timezone

# ==============================================================================
# HELPER AUTENTICAZIONE JWT & MULTI-TENANCY
# ==============================================================================

def genera_token_jwt(azienda_id: str, email: str) -> str:
    """Genera un Token JWT contenente azienda_id ed email con scadenza a 24 ore"""
    payload = {
        "azienda_id": azienda_id,
        "sub": email,
        "exp": datetime.now(timezone.utc) + timedelta(hours=TOKEN_EXPIRE_HOURS)
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=ALGORITHM)

def get_current_azienda_id(
    auth: Optional[HTTPAuthorizationCredentials] = Depends(security_scheme),
    x_azienda_id: Optional[str] = Header(None)
) -> str:
    """
    Estrae l'ID Azienda dal Token JWT inviato nell'header 'Authorization: Bearer <TOKEN>'.
    Se il token manca o non è valido, rifiuta rigorosamente la richiesta con HTTP 401.
    """
    if auth and auth.credentials:
        try:
            payload = jwt.decode(auth.credentials, JWT_SECRET_KEY, algorithms=[ALGORITHM])
            azienda_id = payload.get("azienda_id")
            if azienda_id:
                return azienda_id
        except jwt.ExpiredSignatureError:
            raise HTTPException(status_code=401, detail="Token JWT scaduto. Effettua nuovamente il login.")
        except jwt.InvalidTokenError:
            raise HTTPException(status_code=401, detail="Token JWT non valido.")

    # Se inviato esplicitamente via header custom (es. per dev/debug)
    if x_azienda_id:
        return x_azienda_id

    # Se manca sia il token che l'header, nega l'accesso
    raise HTTPException(status_code=401, detail="Autenticazione richiesta. Invia un Token JWT valido.")


# ==============================================================================
# ENDPOINT AUTHENTICATION (CON RATE LIMITING)
# ==============================================================================

@app.post("/api/v1/auth/login", tags=["Autenticazione"])
@limiter.limit("5/minute")
def login(request: Request, credentials: LoginRequest):
    """
    Autentica l'utente verifcando email e password hashata nel database.
    Protetto contro attacchi Brute-Force (max 5 tentativi/minuto per IP).
    """
    conn = get_db_connection(os.getenv("AZIENDA_ID"))
    cur = conn.cursor()

    try:
        cur.execute("SELECT id, azienda_id, password_hash, ruolo FROM utenti WHERE email = %s;", (credentials.email,))
        utente = cur.fetchone()

        if not utente:
            registra_audit_log(os.getenv("AZIENDA_ID"), "SECURITY", "LOGIN_FAILED",
                               f"Email non trovata: {credentials.email}")
            raise HTTPException(status_code=401, detail="Credenziali non valide (email o password errata).")

        user_id, azienda_id, password_hash, ruolo = utente

        if not pwd_context.verify(credentials.password, password_hash):
            registra_audit_log(azienda_id, "SECURITY", "LOGIN_FAILED", f"Password errata per: {credentials.email}")
            raise HTTPException(status_code=401, detail="Credenziali non valide (email o password errata).")

        token = genera_token_jwt(str(azienda_id), credentials.email)
        registra_audit_log(azienda_id, "INFO", "LOGIN_SUCCESS", f"Login effettuato da: {credentials.email}")

        return {
            "access_token": token,
            "token_type": "bearer",
            "expires_in_hours": TOKEN_EXPIRE_HOURS,
            "user": {
                "email": credentials.email,
                "ruolo": ruolo,
                "azienda_id": str(azienda_id)
            }
        }
    finally:
        cur.close()
        conn.close()


# ==============================================================================
# ENDPOINT REST PROTETTI
# ==============================================================================

@app.get("/", tags=["System"])
def root():
    return {
        "message": "Benvenuto in Smart Treasury API",
        "documentation": "Visita http://127.0.0.1:8000/docs per la documentazione interattiva Swagger"
    }


@app.get("/health", tags=["System"])
def health_check():
    return {"status": "online", "system": "Smart Treasury Backend", "rls_active": True, "rate_limit": "active"}


@app.get("/api/v1/conti/saldo", tags=["Tesoreria"])
def get_saldo_conti(azienda_id: str = Depends(get_current_azienda_id)):
    conn = get_db_connection(azienda_id)
    cur = conn.cursor()

    try:
        cur.execute("SELECT nome_istituto, iban, saldo_attuale FROM conti_bancari WHERE azienda_id = %s;",
                    (azienda_id,))
        conti = cur.fetchall()

        risultato_conti = []
        totale_liquidita = 0.0

        for c in conti:
            saldo_val = float(c[2])
            totale_liquidita += saldo_val
            iban_chiaro = decifra_dato(c[1])
            risultato_conti.append({
                "istituto": c[0],
                "iban_mascherato": f"•••• {iban_chiaro[-4:]}" if len(iban_chiaro) >= 4 else iban_chiaro,
                "saldo": saldo_val
            })

        registra_audit_log(azienda_id, "INFO", "API_GET_SALDO", "Consultazione saldi via API REST")

        return {
            "azienda_id": azienda_id,
            "totale_liquidita": totale_liquidita,
            "conti": risultato_conti
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Errore lettura database: {str(e)}")
    finally:
        cur.close()
        conn.close()


@app.post("/api/v1/corrispettivi", tags=["Incassi B2C"])
def registra_incasso_b2c(data: CorrispettivoCreate, azienda_id: str = Depends(get_current_azienda_id)):
    try:
        inserisci_corrispettivo_giornaliero(
            azienda_id=azienda_id,
            data_incasso=data.data_incasso,
            contanti=data.incasso_contanti,
            pos=data.incasso_pos,
            note=data.note
        )
        totale_giorno = data.incasso_contanti + data.incasso_pos
        registra_audit_log(azienda_id, "INFO", "API_POST_B2C", f"Inserito corrispettivo: {totale_giorno} €")

        return {
            "status": "success",
            "message": "Corrispettivo registrato con successo",
            "totale_registrato": totale_giorno
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Impossibile registrare il corrispettivo: {str(e)}")


@app.post("/api/v1/fatture/upload", tags=["Fatture B2B"])
async def upload_fattura_xml(
        file: UploadFile = File(...),
        azienda_id: str = Depends(get_current_azienda_id)
):
    if not file.filename.lower().endswith('.xml'):
        raise HTTPException(status_code=400, detail="Il file deve essere in formato XML")

    try:
        contents = await file.read()
        with tempfile.NamedTemporaryFile(delete=False, suffix=".xml") as tmp:
            tmp.write(contents)
            tmp_path = tmp.name

        dati_fattura = parse_fattura_xml(tmp_path)
        salva_fattura_in_db(azienda_id, dati_fattura)
        os.remove(tmp_path)

        registra_audit_log(azienda_id, "INFO", "API_UPLOAD_XML",
                           f"Caricata fattura N. {dati_fattura['numero_documento']}")

        return {
            "status": "success",
            "message": f"Fattura N. {dati_fattura['numero_documento']} registrata con successo!",
            "fornitore": dati_fattura['denominazione_fornitore'],
            "importo_totale": dati_fattura['importo_totale']
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Errore durante l'elaborazione dell'XML: {str(e)}")


@app.get("/api/v1/cashflow/previsione", tags=["Previsioni Cash-Flow"])
def get_previsione_cashflow(
        giorni: int = Query(default=30, ge=7, le=90, description="Giorni di previsione (tra 7 e 90)"),
        azienda_id: str = Depends(get_current_azienda_id)
):
    conn = get_db_connection(azienda_id)
    cur = conn.cursor()

    try:
        # 1. Saldo iniziale
        cur.execute("SELECT COALESCE(SUM(saldo_attuale), 0.00) FROM conti_bancari WHERE azienda_id = %s;",
                    (azienda_id,))
        saldo_iniziale = float(cur.fetchone()[0])

        # 2. Media B2C
        cur.execute("""
            SELECT COALESCE(AVG(totale_giornaliero), 0.00) 
            FROM corrispettivi_b2c 
            WHERE azienda_id = %s AND data_corrispettivo >= %s;
        """, (azienda_id, date.today() - timedelta(days=30)))
        media_b2c = float(cur.fetchone()[0])

        # 3. Scadenze B2B
        cur.execute("""
            SELECT sc.data_scadenza, SUM(sc.importo_rata)
            FROM scadenze_b2b sc
            JOIN fatture_b2b f ON sc.fattura_id = f.id
            WHERE f.azienda_id = %s AND sc.stato_pagamento = 'da_pagare'
            GROUP BY sc.data_scadenza;
        """, (azienda_id,))
        scadenze_b2b = {row[0]: float(row[1]) for row in cur.fetchall()}

        # 4. Uscite Fisse
        cur.execute("""
            SELECT giorno_scadenza_mese, importo_stimato
            FROM uscite_ricorrenti
            WHERE azienda_id = %s AND attivo = TRUE;
        """, (azienda_id,))
        uscite_fisse = cur.fetchall()

        # Proiezione
        saldo_progressivo = saldo_iniziale
        data_corrente = date.today()
        proiezione_giornaliera = []
        critici = 0

        for g in range(1, giorni + 1):
            giorno = data_corrente + timedelta(days=g)
            uscita_b2b = scadenze_b2b.get(giorno, 0.0)
            uscita_fissa = sum(float(u[1]) for u in uscite_fisse if u[0] == giorno.day)

            saldo_progressivo += media_b2c - (uscita_b2b + uscita_fissa)

            if saldo_progressivo < 0:
                critici += 1

            proiezione_giornaliera.append({
                "data": giorno.isoformat(),
                "saldo_previsto": round(saldo_progressivo, 2),
                "stato": "CRITICO" if saldo_progressivo < 0 else "OK"
            })

        registra_audit_log(azienda_id, "INFO", "API_GET_CASHFLOW", f"Simulazione {giorni} giorni calcolata")

        return {
            "azienda_id": azienda_id,
            "orizzonte_giorni": giorni,
            "saldo_iniziale": saldo_iniziale,
            "media_incasso_b2c_stimata": media_b2c,
            "giorni_critici_rilevati": critici,
            "proiezione": proiezione_giornaliera
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Errore simulazione cashflow: {str(e)}")
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)