import os
import jwt
import uvicorn
import tempfile
import glob
import shutil
from datetime import date, datetime, timedelta, timezone
from typing import Optional, List

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

# Importazione moduli di sistema personalizzati
import schemas
import registrazione
from db_helper import get_db_connection, decifra_dato, registra_audit_log
from corrispettivi_b2c import inserisci_corrispettivo_giornaliero
from parser_xml import parse_fattura_xml, salva_fattura_in_db

load_dotenv()

# ==============================================================================
# CONFIGURAZIONE SICUREZZA, JWT E RATE LIMITING
# ==============================================================================
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "chiave_segretissima_default")
ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 24

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security_scheme = HTTPBearer(auto_error=False)

limiter = Limiter(key_func=get_remote_address)

# Inizializzazione FastAPI
app = FastAPI(
    title="Smart Treasury API",
    description="Backend SaaS di gestione tesoreria e cash-flow con autenticazione JWT, RLS e Rate Limiting.",
    version="1.2.0"
)

# Registrazione Rate Limiter e CORS
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Permette le chiamate da qualsiasi frontend (risolve il blocco CORS)
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Aggancio del modulo di registrazione (Self-Service)
app.include_router(registrazione.router)


# ==============================================================================
# SCHEMI PYDANTIC LOCALI
# ==============================================================================
class LoginRequest(BaseModel):
    email: EmailStr = Field(..., json_schema_extra={"example": "admin@ferramenta.it"})
    password: str = Field(..., json_schema_extra={"example": "PasswordSicura123!"})


class CorrispettivoCreate(BaseModel):
    data_incasso: date = Field(default_factory=date.today, description="Data dell'incasso")
    incasso_contanti: float = Field(ge=0.0, description="Importo totale in contanti")
    incasso_pos: float = Field(ge=0.0, description="Importo totale via POS")
    note: Optional[str] = Field(default="", description="Note di cassa")


class CorrispettivoGiornoCreate(BaseModel):
    data_incasso: str = Field(..., description="Data nel formato YYYY-MM-DD")
    incasso_contanti: float = Field(default=0.0, ge=0)
    incasso_pos: float = Field(default=0.0, ge=0)


class FatturaAttivaCreate(BaseModel):
    ragione_sociale_cliente: str = Field(..., description="Nome o ragione sociale del cliente")
    partita_iva_cliente: Optional[str] = Field(default=None)
    numero_documento: str = Field(..., description="Numero della fattura emessa")
    data_emissione: str = Field(..., description="Data emissione YYYY-MM-DD")
    importo_totale: float = Field(..., gt=0, description="Importo totale della fattura")


# ==============================================================================
# HELPER AUTENTICAZIONE JWT & MULTI-TENANCY
# ==============================================================================
def genera_token_jwt(azienda_id: str, email: str) -> str:
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
    if auth and auth.credentials:
        try:
            payload = jwt.decode(auth.credentials, JWT_SECRET_KEY, algorithms=[ALGORITHM])
            azienda_id = payload.get("azienda_id")
            if azienda_id:
                return azienda_id
        except jwt.ExpiredSignatureError:
            raise HTTPException(status_code=401, detail="Token JWT scaduto.")
        except jwt.InvalidTokenError:
            raise HTTPException(status_code=401, detail="Token JWT non valido.")

    if x_azienda_id:
        return x_azienda_id

    raise HTTPException(status_code=401, detail="Autenticazione richiesta. Invia un Token JWT valido.")


# ==============================================================================
# ENDPOINT: ROOT E SYSTEM
# ==============================================================================
@app.get("/", tags=["System"])
def root():
    return {"message": "Benvenuto in Smart Treasury API. Visita /docs per la documentazione."}


@app.get("/health", tags=["System"])
def health_check():
    return {"status": "online", "system": "Smart Treasury Backend", "rls_active": True}


# ==============================================================================
# ENDPOINT: AUTENTICAZIONE
# ==============================================================================
@app.post("/api/v1/auth/login", tags=["Autenticazione"])
@limiter.limit("5/minute")
def login(request: Request, credentials: LoginRequest):
    # In produzione, l'azienda ID per il login va ricavato diversamente, ma per ora lo leggiamo dall'utente
    conn = get_db_connection(os.getenv("AZIENDA_ID", "default"))
    cur = conn.cursor()

    try:
        cur.execute("SELECT id, azienda_id, password_hash, ruolo FROM utenti WHERE email = %s;", (credentials.email,))
        utente = cur.fetchone()

        if not utente:
            raise HTTPException(status_code=401, detail="Credenziali non valide.")

        user_id, azienda_id, password_hash, ruolo = utente

        if not pwd_context.verify(credentials.password, password_hash):
            raise HTTPException(status_code=401, detail="Credenziali non valide.")

        token = genera_token_jwt(str(azienda_id), credentials.email)
        registra_audit_log(azienda_id, "INFO", "LOGIN_SUCCESS", f"Login effettuato da: {credentials.email}")

        return {
            "access_token": token,
            "token_type": "bearer",
            "expires_in_hours": TOKEN_EXPIRE_HOURS,
            "user": {"email": credentials.email, "ruolo": ruolo, "azienda_id": str(azienda_id)}
        }
    finally:
        cur.close()
        conn.close()


# ==============================================================================
# ENDPOINT: TESORERIA E CASH FLOW
# ==============================================================================
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
        return {"azienda_id": azienda_id, "totale_liquidita": totale_liquidita, "conti": risultato_conti}
    finally:
        cur.close()
        conn.close()


@app.get("/api/v1/cashflow/previsione", tags=["Previsioni Cash-Flow"])
def get_previsione_cashflow(giorni: int = Query(default=30, ge=7, le=90),
                            azienda_id: str = Depends(get_current_azienda_id)):
    conn = get_db_connection(azienda_id)
    cur = conn.cursor()
    try:
        cur.execute("SELECT COALESCE(SUM(saldo_attuale), 0.00) FROM conti_bancari WHERE azienda_id = %s;",
                    (azienda_id,))
        saldo_iniziale = float(cur.fetchone()[0] or 0)

        cur.execute("""
            SELECT COALESCE(AVG(totale_giornaliero), 0.00) 
            FROM corrispettivi_b2c 
            WHERE azienda_id = %s AND data_corrispettivo >= %s;
        """, (azienda_id, date.today() - timedelta(days=30)))
        media_b2c = float(cur.fetchone()[0] or 0)

        cur.execute("""
            SELECT sc.data_scadenza, SUM(sc.importo_rata)
            FROM scadenze_b2b sc
            JOIN fatture_b2b f ON sc.fattura_id = f.id
            WHERE f.azienda_id = %s AND sc.stato_pagamento = 'da_pagare'
            GROUP BY sc.data_scadenza;
        """, (azienda_id,))
        scadenze_b2b = {row[0]: float(row[1]) for row in cur.fetchall()}

        cur.execute(
            "SELECT giorno_scadenza_mese, importo_stimato FROM uscite_ricorrenti WHERE azienda_id = %s AND attivo = TRUE;",
            (azienda_id,))
        uscite_fisse = cur.fetchall()

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

        return {
            "azienda_id": azienda_id,
            "orizzonte_giorni": giorni,
            "saldo_iniziale": saldo_iniziale,
            "media_incasso_b2c_stimata": media_b2c,
            "giorni_critici_rilevati": critici,
            "proiezione": proiezione_giornaliera
        }
    finally:
        cur.close()
        conn.close()


# ==============================================================================
# ENDPOINT: FATTURE B2B, SDI E SCADENZE PASSIVE
# ==============================================================================
@app.post("/api/v1/fatture/upload", tags=["Fatture B2B"])
async def upload_fattura_xml(file: UploadFile = File(...), azienda_id: str = Depends(get_current_azienda_id)):
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
        return {"status": "success", "fornitore": dati_fattura['denominazione_fornitore']}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/fatture/sync-sdi", tags=["Fatture B2B (Automazione)"])
def sincronizza_fatture_sdi(azienda_id: str = Depends(get_current_azienda_id)):
    cartella_inbox = "inbox_xml"
    cartella_archivio = "archivio_xml"
    os.makedirs(cartella_inbox, exist_ok=True)
    os.makedirs(cartella_archivio, exist_ok=True)
    file_xml = glob.glob(os.path.join(cartella_inbox, "*.xml"))

    if not file_xml:
        return {"status": "success", "messaggio": "Nessuna nuova fattura trovata.", "importate": 0}

    importate_count = 0
    for percorso_file in file_xml:
        try:
            dati_fattura = parse_fattura_xml(percorso_file)
            salva_fattura_in_db(azienda_id, dati_fattura)
            shutil.move(percorso_file, os.path.join(cartella_archivio, os.path.basename(percorso_file)))
            importate_count += 1
        except Exception:
            pass
    return {"status": "success", "importate": importate_count}


@app.get("/api/v1/scadenze", tags=["Scadenze B2B"])
def leggi_scadenze(stato: Optional[str] = Query(default=None), azienda_id: str = Depends(get_current_azienda_id)):
    conn = get_db_connection(azienda_id)
    cur = conn.cursor()
    try:
        query = """
            SELECT sc.id, sc.fattura_id, f.numero_documento, fornitore.ragione_sociale, 
                   sc.data_scadenza, sc.importo_rata, sc.stato_pagamento
            FROM scadenze_b2b sc
            JOIN fatture_b2b f ON sc.fattura_id = f.id
            LEFT JOIN fornitori fornitore ON f.fornitore_id = fornitore.id
            WHERE f.azienda_id = %s
        """
        params = [azienda_id]
        if stato:
            query += " AND sc.stato_pagamento = %s"
            params.append(stato)
        query += " ORDER BY sc.data_scadenza ASC;"
        cur.execute(query, tuple(params))

        colonne = [desc[0] for desc in cur.description]
        return [dict(zip(colonne, riga)) for riga in cur.fetchall()]
    finally:
        cur.close()
        conn.close()


@app.patch("/api/v1/scadenze/{scadenza_id}/paga", tags=["Scadenze B2B"])
def marca_scadenza_pagata(scadenza_id: int, azienda_id: str = Depends(get_current_azienda_id)):
    conn = get_db_connection(azienda_id)
    cur = conn.cursor()
    try:
        cur.execute("UPDATE scadenze_b2b SET stato_pagamento = 'pagato' WHERE id = %s;", (scadenza_id,))
        conn.commit()
        return {"status": "success", "message": f"Scadenza ID {scadenza_id} pagata."}
    finally:
        cur.close()
        conn.close()


# ==============================================================================
# ENDPOINT: CICLO ATTIVO E CORRISPETTIVI
# ==============================================================================
@app.post("/api/v1/corrispettivi/registra", tags=["Corrispettivi B2C"])
def registra_corrispettivo_giornaliero_endpoint(dati: CorrispettivoGiornoCreate,
                                                azienda_id: str = Depends(get_current_azienda_id)):
    conn = get_db_connection(azienda_id)
    cur = conn.cursor()
    try:
        totale_giornaliero = dati.incasso_contanti + dati.incasso_pos
        cur.execute("""
            INSERT INTO corrispettivi_b2c (azienda_id, data_incasso, data_corrispettivo, importo_contanti, importo_pos, importo_totale)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (azienda_id, data_incasso) 
            DO UPDATE SET importo_contanti = EXCLUDED.importo_contanti, importo_pos = EXCLUDED.importo_pos, importo_totale = EXCLUDED.importo_totale;
        """, (azienda_id, dati.data_incasso, dati.data_incasso, dati.incasso_contanti, dati.incasso_pos,
              totale_giornaliero))
        conn.commit()
        return {"status": "success", "totale": totale_giornaliero}
    finally:
        cur.close()
        conn.close()


@app.post("/api/v1/fatture-attive", tags=["Ciclo Attivo"])
def crea_fattura_attiva(dati: FatturaAttivaCreate, azienda_id: str = Depends(get_current_azienda_id)):
    conn = get_db_connection(azienda_id)
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO clienti (azienda_id, ragione_sociale, partita_iva) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING;",
            (azienda_id, dati.ragione_sociale_cliente, dati.partita_iva_cliente))
        cur.execute("SELECT id FROM clienti WHERE ragione_sociale = %s AND azienda_id = %s;",
                    (dati.ragione_sociale_cliente, azienda_id))
        cliente_id = cur.fetchone()[0]

        cur.execute("""
            INSERT INTO fatture_attive (azienda_id, cliente_id, numero_documento, data_emissione, importo_totale, stato_incasso)
            VALUES (%s, %s, %s, %s, %s, 'da_incassare') RETURNING id;
        """, (azienda_id, cliente_id, dati.numero_documento, dati.data_emissione, dati.importo_totale))

        fattura_id = cur.fetchone()[0]
        conn.commit()
        return {"status": "success", "fattura_id": fattura_id}
    finally:
        cur.close()
        conn.close()


@app.get("/api/v1/fatture-attive", tags=["Ciclo Attivo"])
def elenco_fatture_attive(stato: Optional[str] = Query(default=None),
                          azienda_id: str = Depends(get_current_azienda_id)):
    conn = get_db_connection(azienda_id)
    cur = conn.cursor()
    try:
        query = """
            SELECT f.id, f.numero_documento, c.ragione_sociale as cliente, f.data_emissione, f.importo_totale, f.stato_incasso
            FROM fatture_attive f JOIN clienti c ON f.cliente_id = c.id WHERE f.azienda_id = %s
        """
        params = [azienda_id]
        if stato:
            query += " AND f.stato_incasso = %s"
            params.append(stato)
        query += " ORDER BY f.data_emissione DESC;"
        cur.execute(query, tuple(params))

        colonne = [desc[0] for desc in cur.description]
        return [dict(zip(colonne, riga)) for riga in cur.fetchall()]
    finally:
        cur.close()
        conn.close()


@app.patch("/api/v1/fatture-attive/{fattura_id}/incassa", tags=["Ciclo Attivo"])
def marca_fattura_incassata(fattura_id: int, azienda_id: str = Depends(get_current_azienda_id)):
    conn = get_db_connection(azienda_id)
    cur = conn.cursor()
    try:
        cur.execute("UPDATE fatture_attive SET stato_incasso = 'incassato' WHERE id = %s AND azienda_id = %s;",
                    (fattura_id, azienda_id))
        conn.commit()
        return {"status": "success"}
    finally:
        cur.close()
        conn.close()


# ==============================================================================
# ENDPOINT: ANAGRAFICHE CLIENTI E FORNITORI E USCITE FISSE
# ==============================================================================
@app.get("/api/v1/clienti", tags=["Clienti B2B"])
def leggi_clienti(azienda_id: str = Depends(get_current_azienda_id)):
    conn = get_db_connection(azienda_id)
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM clienti WHERE azienda_id = %s;", (azienda_id,))
        colonne = [desc[0] for desc in cur.description]
        return [dict(zip(colonne, riga)) for riga in cur.fetchall()]
    finally:
        cur.close()
        conn.close()


@app.get("/api/v1/fornitori", tags=["Fornitori B2B"])
def leggi_fornitori(azienda_id: str = Depends(get_current_azienda_id)):
    conn = get_db_connection(azienda_id)
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM fornitori WHERE azienda_id = %s;", (azienda_id,))
        colonne = [desc[0] for desc in cur.description]
        return [dict(zip(colonne, riga)) for riga in cur.fetchall()]
    finally:
        cur.close()
        conn.close()


@app.get("/api/v1/uscite-ricorrenti", tags=["Uscite Ricorrenti"])
def leggi_uscite_ricorrenti(azienda_id: str = Depends(get_current_azienda_id)):
    conn = get_db_connection(azienda_id)
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM uscite_ricorrenti WHERE azienda_id = %s;", (azienda_id,))
        colonne = [desc[0] for desc in cur.description]
        return [dict(zip(colonne, riga)) for riga in cur.fetchall()]
    finally:
        cur.close()
        conn.close()


# ==============================================================================
# AVVIO SERVER (DEVE STARE SEMPRE ALLA FINE)
# ==============================================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)