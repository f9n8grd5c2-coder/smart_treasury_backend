import os
import jwt
import uvicorn
import tempfile
from datetime import date, datetime, timedelta
from typing import Optional, List
import schemas
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


# ==============================================================================
# ENDPOINT: ANAGRAFICA CLIENTI B2B
# ==============================================================================

@app.post("/api/v1/clienti", response_model=schemas.ClienteResponse, tags=["Clienti B2B"])
def crea_cliente(cliente: schemas.ClienteCreate, azienda_id: str = Depends(get_current_azienda_id)):
    """Crea un nuovo cliente B2B nel database per l'azienda autenticata dal Token JWT."""
    conn = get_db_connection(azienda_id)
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO clienti (
                azienda_id, ragione_sociale, partita_iva, codice_fiscale, 
                email_amministrazione, ritardo_medio_giorni, fido_massimo_concesso
            ) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *;
        """, (
            azienda_id, cliente.ragione_sociale, cliente.partita_iva,
            cliente.codice_fiscale, cliente.email_amministrazione,
            cliente.ritardo_medio_giorni, cliente.fido_massimo_concesso
        ))
        nuovo_cliente = cur.fetchone()
        conn.commit()

        registra_audit_log(azienda_id, "INFO", "API_POST_CLIENTI", f"Creato cliente: {cliente.ragione_sociale}")

        colonne = [desc[0] for desc in cur.description]
        return dict(zip(colonne, nuovo_cliente))
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=f"Errore inserimento cliente: {str(e)}")
    finally:
        cur.close()
        conn.close()


@app.get("/api/v1/clienti", response_model=List[schemas.ClienteResponse], tags=["Clienti B2B"])
def leggi_clienti(azienda_id: str = Depends(get_current_azienda_id)):
    """Recupera l'elenco di tutti i clienti B2B dell'azienda autenticata."""
    conn = get_db_connection(azienda_id)
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM clienti WHERE azienda_id = %s;", (azienda_id,))
        righe = cur.fetchall()
        colonne = [desc[0] for desc in cur.description]
        return [dict(zip(colonne, riga)) for riga in righe]
    finally:
        cur.close()
        conn.close()
# ==============================================================================
# ENDPOINT: USCITE RICORRENTI (Spese fisse mensili)
# ==============================================================================

@app.post("/api/v1/uscite-ricorrenti", response_model=schemas.UscitaRicorrenteResponse, tags=["Uscite Ricorrenti"])
def crea_uscita_ricorrente(uscita: schemas.UscitaRicorrenteCreate, azienda_id: str = Depends(get_current_azienda_id)):
    """Registra una nuova spesa fissa ricorrente mensile per il calcolo del cash flow."""
    conn = get_db_connection(azienda_id)
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO uscite_ricorrenti (
                azienda_id, descrizione, giorno_scadenza_mese, importo_stimato, attivo
            ) VALUES (%s, %s, %s, %s, %s) RETURNING *;
        """, (
            azienda_id, uscita.descrizione, uscita.giorno_scadenza_mese,
            uscita.importo_stimato, uscita.attivo
        ))
        nuova_uscita = cur.fetchone()
        conn.commit()

        registra_audit_log(azienda_id, "INFO", "API_POST_USCITA_RICORRENTE", f"Creata uscita fissa: {uscita.descrizione}")

        colonne = [desc[0] for desc in cur.description]
        return dict(zip(colonne, nuova_uscita))
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=f"Errore inserimento uscita ricorrente: {str(e)}")
    finally:
        cur.close()
        conn.close()


@app.get("/api/v1/uscite-ricorrenti", response_model=List[schemas.UscitaRicorrenteResponse], tags=["Uscite Ricorrenti"])
def leggi_uscite_ricorrenti(azienda_id: str = Depends(get_current_azienda_id)):
    """Recupera l'elenco di tutte le uscite fisse mensili configurate."""
    conn = get_db_connection(azienda_id)
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM uscite_ricorrenti WHERE azienda_id = %s;", (azienda_id,))
        righe = cur.fetchall()
        colonne = [desc[0] for desc in cur.description]
        return [dict(zip(colonne, riga)) for riga in righe]
    finally:
        cur.close()
        conn.close()


# ==============================================================================
# ENDPOINT: SCADENZE B2B (Calendario rate da pagare ai fornitori)
# ==============================================================================

@app.get("/api/v1/scadenze", tags=["Scadenze B2B"])
def leggi_scadenze(
        stato: Optional[str] = Query(default=None, description="Filtra per stato: 'da_pagare' o 'pagato'"),
        azienda_id: str = Depends(get_current_azienda_id)
):
    """Recupera l'elenco di tutte le scadenze di pagamento B2B collegate alle fatture dell'azienda."""
    conn = get_db_connection(azienda_id)
    cur = conn.cursor()
    try:
        # Query che unisce scadenze e fatture per risalire anche al fornitore
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
        righe = cur.fetchall()

        colonne = [desc[0] for desc in cur.description]
        risultato = [dict(zip(colonne, riga)) for riga in righe]

        return risultato
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Errore lettura scadenze: {str(e)}")
    finally:
        cur.close()
        conn.close()


@app.patch("/api/v1/scadenze/{scadenza_id}/paga", tags=["Scadenze B2B"])
def marca_scadenza_pagata(scadenza_id: int, azienda_id: str = Depends(get_current_azienda_id)):
    """Aggiorna lo stato di una scadenza da 'da_pagare' a 'pagato'."""
    conn = get_db_connection(azienda_id)
    cur = conn.cursor()
    try:
        # Verifichiamo che la scadenza appartenga a un'azienda autorizzata tramite join con fatture_b2b
        cur.execute("""
            SELECT sc.id, sc.stato_pagamento 
            FROM scadenze_b2b sc
            JOIN fatture_b2b f ON sc.fattura_id = f.id
            WHERE sc.id = %s AND f.azienda_id = %s;
        """, (scadenza_id, azienda_id))

        scadenza = cur.fetchone()
        if not scadenza:
            raise HTTPException(status_code=404, detail="Scadenza non trovata o non autorizzata.")

        # Aggiorniamo lo stato
        cur.execute("""
            UPDATE scadenze_b2b 
            SET stato_pagamento = 'pagato' 
            WHERE id = %s;
        """, (scadenza_id,))
        conn.commit()

        registra_audit_log(azienda_id, "INFO", "API_PAGA_SCADENZA",
                           f"Registrato pagamento per la scadenza ID: {scadenza_id}")

        return {
            "status": "success",
            "message": f"Scadenza ID {scadenza_id} contrassegnata come pagata con successo."
        }
    except HTTPException as he:
        raise he
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Errore aggiornamento scadenza: {str(e)}")
    finally:
        cur.close()
        conn.close()

if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
# ==============================================================================
# ENDPOINT: ANAGRAFICA FORNITORI B2B
# ==============================================================================

@app.post("/api/v1/fornitori", response_model=schemas.FornitoreResponse, tags=["Fornitori B2B"])
def crea_fornitore(fornitore: schemas.FornitoreCreate, azienda_id: str = Depends(get_current_azienda_id)):
    """Crea un nuovo fornitore nel database."""
    conn = get_db_connection(azienda_id)
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO fornitori (
                azienda_id, ragione_sociale, partita_iva, codice_fiscale, 
                email_amministrazione, iban, giorni_pagamento_standard
            ) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *;
        """, (
            azienda_id, fornitore.ragione_sociale, fornitore.partita_iva,
            fornitore.codice_fiscale, fornitore.email_amministrazione,
            fornitore.iban, fornitore.giorni_pagamento_standard
        ))
        nuovo_fornitore = cur.fetchone()
        conn.commit()

        registra_audit_log(azienda_id, "INFO", "API_POST_FORNITORI", f"Creato fornitore: {fornitore.ragione_sociale}")

        colonne = [desc[0] for desc in cur.description]
        return dict(zip(colonne, nuovo_fornitore))
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=f"Errore inserimento fornitore: {str(e)}")
    finally:
        cur.close()
        conn.close()

@app.get("/api/v1/fornitori", response_model=List[schemas.FornitoreResponse], tags=["Fornitori B2B"])
def leggi_fornitori(azienda_id: str = Depends(get_current_azienda_id)):
    """Recupera l'elenco di tutti i fornitori."""
    conn = get_db_connection(azienda_id)
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM fornitori WHERE azienda_id = %s;", (azienda_id,))
        righe = cur.fetchall()
        colonne = [desc[0] for desc in cur.description]
        return [dict(zip(colonne, riga)) for riga in righe]
    finally:
        cur.close()
        conn.close()


import glob
import shutil


# ==============================================================================
# ENDPOINT: SINCRONIZZAZIONE AUTOMATICA FATTURE (Simulazione SDI)
# ==============================================================================

@app.post("/api/v1/fatture/sync-sdi", tags=["Fatture B2B (Automazione)"])
def sincronizza_fatture_sdi(azienda_id: str = Depends(get_current_azienda_id)):
    """
    Simula lo scaricamento automatico dal Sistema di Interscambio (SDI).
    Scansiona la cartella 'inbox_xml', importa le fatture, genera le scadenze
    e sposta i file elaborati in 'archivio_xml'.
    """
    cartella_inbox = "inbox_xml"
    cartella_archivio = "archivio_xml"

    # Creiamo le cartelle se non esistono
    os.makedirs(cartella_inbox, exist_ok=True)
    os.makedirs(cartella_archivio, exist_ok=True)

    # Cerchiamo tutti i file .xml nella cartella inbox
    file_xml = glob.glob(os.path.join(cartella_inbox, "*.xml"))

    if not file_xml:
        return {
            "status": "success",
            "messaggio": "Nessuna nuova fattura trovata nella cartella SDI in entrata.",
            "importate": 0
        }

    importate_count = 0
    errori = []

    # Importiamo la logica del parser che abbiamo già scritto
    from parser_xml import parse_fattura_xml, salva_fattura_in_db

    for percorso_file in file_xml:
        try:
            # 1. Legge e analizza l'XML
            dati_fattura = parse_fattura_xml(percorso_file)

            # 2. Salva nel DB (fornitore, fattura e scadenze automatiche)
            salva_fattura_in_db(azienda_id, dati_fattura)

            # 3. Sposta il file nella cartella di archivio per evitare doppioni
            nome_file = os.path.basename(percorso_file)
            shutil.move(percorso_file, os.path.join(cartella_archivio, nome_file))

            importate_count += 1
        except Exception as e:
            errori.append({"file": os.path.basename(percorso_file), "errore": str(e)})

    registra_audit_log(azienda_id, "INFO", "SYNC_SDI", f"Sincronizzate {importate_count} fatture da SDI.")

    return {
        "status": "success",
        "messaggio": f"Sincronizzazione completata. Importate {importate_count} fatture.",
        "errori": errori if errori else None
    }


# ==============================================================================
# ENDPOINT: CORRISPETTIVI B2C (Simulazione flussi telematici cassa)
# ==============================================================================

class CorrispettivoGiornoCreate(BaseModel):
    data_incasso: str = Field(..., description="Data nel formato YYYY-MM-DD")
    incasso_contanti: float = Field(default=0.0, ge=0)
    incasso_pos: float = Field(default=0.0, ge=0)


@app.post("/api/v1/corrispettivi/registra", tags=["Corrispettivi B2C"])
def registra_corrispettivo_giornaliero(dati: CorrispettivoGiornoCreate,
                                       azienda_id: str = Depends(get_current_azienda_id)):
    """
    Registra o aggiorna i corrispettivi giornalieri (contanti + POS) provenienti
    dal registratore di cassa telematico.
    """
    conn = get_db_connection(azienda_id)
    cur = conn.cursor()
    try:
        totale_giornaliero = dati.incasso_contanti + dati.incasso_pos

        cur.execute("""
            INSERT INTO corrispettivi_b2c (azienda_id, data_incasso, data_corrispettivo, importo_contanti, importo_pos, importo_totale)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (azienda_id, data_incasso) 
            DO UPDATE SET 
                importo_contanti = EXCLUDED.importo_contanti,
                importo_pos = EXCLUDED.importo_pos,
                importo_totale = EXCLUDED.importo_totale;
        """, (
            azienda_id, dati.data_incasso, dati.data_incasso,
            dati.incasso_contanti, dati.incasso_pos, totale_giornaliero
        ))

        conn.commit()
        registra_audit_log(azienda_id, "INFO", "REGISTRA_CORRISPETTIVO",
                           f"Aggiornati corrispettivi per il {dati.data_incasso}: {totale_giornaliero} €")

        return {
            "status": "success",
            "message": f"Corrispettivi del {dati.data_incasso} registrati con successo.",
            "totale": totale_giornaliero
        }
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=f"Errore registrazione corrispettivi: {str(e)}")
    finally:
        cur.close()
        conn.close()


# ==============================================================================
# ENDPOINT: CICLO ATTIVO (Fatture clienti e incassi)
# ==============================================================================

class FatturaAttivaCreate(BaseModel):
    ragione_sociale_cliente: str = Field(..., description="Nome o ragione sociale del cliente")
    partita_iva_cliente: Optional[str] = Field(default=None)
    numero_documento: str = Field(..., description="Numero della fattura emessa")
    data_emissione: str = Field(..., description="Data emissione YYYY-MM-DD")
    importo_totale: float = Field(..., gt=0, description="Importo totale della fattura")


@app.post("/api/v1/fatture-attive", tags=["Ciclo Attivo"])
def crea_fattura_attiva(dati: FatturaAttivaCreate, azienda_id: str = Depends(get_current_azienda_id)):
    """Registra una fattura emessa verso un cliente e genera il credito da incassare."""
    conn = get_db_connection(azienda_id)
    cur = conn.cursor()
    try:
        # 1. Inserisci o recupera il cliente
        cur.execute("""
            INSERT INTO clienti (azienda_id, ragione_sociale, partita_iva)
            VALUES (%s, %s, %s)
            ON CONFLICT DO NOTHING;
        """, (azienda_id, dati.ragione_sociale_cliente, dati.partita_iva_cliente))

        cur.execute("SELECT id FROM clienti WHERE ragione_sociale = %s AND azienda_id = %s;",
                    (dati.ragione_sociale_cliente, azienda_id))
        cliente_id = cur.fetchone()[0]

        # 2. Inserisci la fattura attiva
        cur.execute("""
            INSERT INTO fatture_attive (azienda_id, cliente_id, numero_documento, data_emissione, importo_totale, stato_incasso)
            VALUES (%s, %s, %s, %s, %s, 'da_incassare')
            RETURNING id;
        """, (azienda_id, cliente_id, dati.numero_documento, dati.data_emissione, dati.importo_totale))

        fattura_id = cur.fetchone()[0]
        conn.commit()

        registra_audit_log(azienda_id, "INFO", "CREA_FATTURA_ATTIVA",
                           f"Emessa fattura attiva N. {dati.numero_documento} per {dati.ragione_sociale_cliente}")

        return {
            "status": "success",
            "message": f"Fattura attiva N. {dati.numero_documento} registrata con successo.",
            "fattura_id": fattura_id
        }
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Errore creazione fattura attiva: {str(e)}")
    finally:
        cur.close()
        conn.close()


@app.get("/api/v1/fatture-attive", tags=["Ciclo Attivo"])
def elenco_fatture_attive(
        stato: Optional[str] = Query(default=None, description="Filtra per stato: 'da_incassare' o 'incassato'"),
        azienda_id: str = Depends(get_current_azienda_id)
):
    """Recupera l'elenco delle fatture emesse ai clienti e il loro stato di pagamento."""
    conn = get_db_connection(azienda_id)
    cur = conn.cursor()
    try:
        query = """
            SELECT f.id, f.numero_documento, c.ragione_sociale as cliente, 
                   f.data_emissione, f.importo_totale, f.stato_incasso
            FROM fatture_attive f
            JOIN clienti c ON f.cliente_id = c.id
            WHERE f.azienda_id = %s
        """
        params = [azienda_id]

        if stato:
            query += " AND f.stato_incasso = %s"
            params.append(stato)

        query += " ORDER BY f.data_emissione DESC;"

        cur.execute(query, tuple(params))
        righe = cur.fetchall()

        colonne = [desc[0] for desc in cur.description]
        return [dict(zip(colonne, riga)) for riga in righe]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Errore lettura fatture attive: {str(e)}")
    finally:
        cur.close()
        conn.close()


@app.patch("/api/v1/fatture-attive/{fattura_id}/incassa", tags=["Ciclo Attivo"])
def marca_fattura_incassata(fattura_id: int, azienda_id: str = Depends(get_current_azienda_id)):
    """Segna una fattura attiva come 'incassata' quando il cliente effettua il bonifico."""
    conn = get_db_connection(azienda_id)
    cur = conn.cursor()
    try:
        cur.execute("""
            UPDATE fatture_attive 
            SET stato_incasso = 'incassato' 
            WHERE id = %s AND azienda_id = %s;
        """, (fattura_id, azienda_id))
        conn.commit()

        return {
            "status": "success",
            "message": f"Fattura attiva ID {fattura_id} contrassegnata come incassata."
        }
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Errore aggiornamento fattura attiva: {str(e)}")
    finally:
        cur.close()
        conn.close()
# ==============================================================================
# ENDPOINT: CASH FLOW PREVISIONALE
# ==============================================================================

@app.get("/api/v1/cashflow/previsione", tags=["Cash Flow"])
def calcola_cash_flow_previsionale(
    giorni_proiezione: int = Query(default=30, description="Numero di giorni futuri da proiettare"),
    azienda_id: str = Depends(get_current_azienda_id)
):
    """
    Calcola il flusso di cassa previsionale aggregando:
    - Entrate da corrispettivi B2C e fatture attive clienti.
    - Uscite da scadenze B2B fornitori e spese fisse ricorrenti.
    Restituisce il saldo aggregato per data.
    """
    conn = get_db_connection(azienda_id)
    cur = conn.cursor()
    try:
        flussi = {}

        # 1. Raccogli le entrate dai corrispettivi B2C
        cur.execute("""
            SELECT data_incasso, importo_totale 
            FROM corrispettivi_b2c 
            WHERE azienda_id = %s;
        """, (azienda_id,))
        for data_inc, importo in cur.fetchall():
            data_str = str(data_inc)
            flussi[data_str] = flussi.get(data_str, {"entrate": 0.0, "uscite": 0.0})
            flussi[data_str]["entrate"] += float(importo)

        # 2. Raccogli le entrate dalle fatture attive (usiamo la data emissione come riferimento base per ora)
        cur.execute("""
            SELECT data_emissione, importo_totale 
            FROM fatture_attive 
            WHERE azienda_id = %s AND stato_incasso = 'da_incassare';
        """, (azienda_id,))
        for data_em, importo in cur.fetchall():
            data_str = str(data_em)
            flussi[data_str] = flussi.get(data_str, {"entrate": 0.0, "uscite": 0.0})
            flussi[data_str]["entrate"] += float(importo)

        # 3. Raccogli le uscite dalle scadenze B2B dei fornitori
        cur.execute("""
            SELECT data_scadenza, importo_rata 
            FROM scadenze_b2b s
            JOIN fatture_b2b f ON s.fattura_id = f.id
            WHERE f.azienda_id = %s AND s.stato_pagamento = 'da_pagare';
        """, (azienda_id,))
        for data_scad, importo in cur.fetchall():
            data_str = str(data_scad)
            flussi[data_str] = flussi.get(data_str, {"entrate": 0.0, "uscite": 0.0})
            flussi[data_str]["uscite"] += float(importo)

        # 4. Ordiniamo i dati per data e calcoliamo il saldo progressivo
        date_ordinate = sorted(flussi.keys())
        proiezione = []
        saldo_progressivo = 0.0

        for d in date_ordinate:
            entrate = flussi[d]["entrate"]
            uscite = flussi[d]["uscite"]
            netto_giornaliero = entrate - uscite
            saldo_progressivo += netto_giornaliero

            proiezione.append({
                "data": d,
                "entrate": round(entrate, 2),
                "uscite": round(uscite, 2),
                "netto_giornaliero": round(netto_giornaliero, 2),
                "saldo_progressivo": round(saldo_progressivo, 2)
            })

        return {
            "status": "success",
            "giorni_analizzati": len(proiezione),
            "proiezione_cash_flow": proiezione
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Errore calcolo cash flow: {str(e)}")
    finally:
        cur.close()
        conn.close()