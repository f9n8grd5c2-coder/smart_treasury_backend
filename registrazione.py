import uuid
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr, Field
from passlib.context import CryptContext
from db_helper import get_db_connection

# Creiamo un "Router" che raggrupperà tutti gli endpoint di registrazione
router = APIRouter(prefix="/api/v1/auth", tags=["Registrazione e Onboarding"])

# Configurazione per cifrare le password (uguale al main.py)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# Schema dei dati che il cliente o tu invierete per registrarvi
class RegistrazioneRequest(BaseModel):
    ragione_sociale: str = Field(..., description="Nome dell'azienda")
    partita_iva: str = Field(..., description="Partita IVA a 11 cifre")
    email: EmailStr = Field(..., description="Email per il login")
    password: str = Field(..., min_length=8, description="Password sicura (min. 8 caratteri)")


@router.post("/register")
def registra_nuova_azienda(dati: RegistrazioneRequest):
    """
    Endpoint per la registrazione: utilizzabile sia in self-service dal sito web
    sia manualmente da te per creare l'account a un cliente.
    """
    # 1. Genera un ID azienda unico (es. az_4f8a9b2c)
    nuovo_azienda_id = f"az_{uuid.uuid4().hex[:8]}"

    # 2. Cifra la password in modo irreversibile
    password_hash = pwd_context.hash(dati.password)

    # 3. Ci colleghiamo al database
    conn = get_db_connection(nuovo_azienda_id)
    cur = conn.cursor()

    try:
        # Controlliamo che l'email non sia già registrata
        cur.execute("SELECT id FROM utenti WHERE email = %s;", (dati.email,))
        if cur.fetchone():
            raise HTTPException(status_code=400, detail="Questa email è già registrata nel sistema.")

        # Inseriamo i dati dell'azienda (se hai una tabella 'aziende', altrimenti puoi omettere questo blocco)
        cur.execute("""
            INSERT INTO aziende (id, ragione_sociale, partita_iva)
            VALUES (%s, %s, %s)
            ON CONFLICT DO NOTHING;
        """, (nuovo_azienda_id, dati.ragione_sociale, dati.partita_iva))

        # Inseriamo l'utente amministratore dell'azienda
        cur.execute("""
            INSERT INTO utenti (azienda_id, email, password_hash, ruolo)
            VALUES (%s, %s, %s, 'admin');
        """, (nuovo_azienda_id, dati.email, password_hash))

        conn.commit()

        return {
            "status": "success",
            "message": "Azienda registrata con successo!",
            "dati_accesso": {
                "azienda_id": nuovo_azienda_id,
                "email": dati.email
            }
        }

    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Errore durante la creazione dell'account: {str(e)}")
    finally:
        cur.close()
        conn.close()