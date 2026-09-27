import os
from dotenv import load_dotenv
from passlib.context import CryptContext
from db_helper import get_db_connection

load_dotenv()

AZIENDA_ID = os.getenv("AZIENDA_ID")

# Configurazione hashing password con bcrypt
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def crea_utente_admin(email: str, password_chiara: str):
    """Cifra la password ed inserisce un nuovo utente nel DB"""

    # 1. Cifratura (hashing) della password
    password_hash = pwd_context.hash(password_chiara)

    # 2. Connessione al DB con RLS
    conn = get_db_connection(AZIENDA_ID)
    cur = conn.cursor()

    try:
        cur.execute("""
            INSERT INTO utenti (azienda_id, email, password_hash, ruolo)
            VALUES (%s, %s, %s, 'admin')
            ON CONFLICT (email) DO UPDATE 
            SET password_hash = EXCLUDED.password_hash;
        """, (AZIENDA_ID, email, password_hash))

        conn.commit()
        print(f"✅ Utente {email} creato/aggiornato con successo nel database!")
        print(f"🔒 Password cifrata (Hash): {password_hash[:25]}...")
    except Exception as e:
        conn.rollback()
        print(f"❌ Errore durante l'inserimento: {e}")
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    # Credenziali di test
    EMAIL_TEST = "admin@ferramenta.it"
    PASSWORD_TEST = "PasswordSicura123!"

    print("🔑 Creazione utente di test in corso...")
    crea_utente_admin(EMAIL_TEST, PASSWORD_TEST)