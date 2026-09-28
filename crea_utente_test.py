import os
import psycopg2
from passlib.context import CryptContext
from db_helper import get_db_connection

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

EMAIL_TEST = "admin@ferramenta.it"
PASSWORD_TEST = "PasswordSicura123!"
AZIENDA_ID = os.getenv("AZIENDA_ID", "az_001_ferramenta")

def inizializza_db_e_utente():
    print("🔑 Inizializzazione struttura DB e utente di test in corso...")
    conn = get_db_connection(AZIENDA_ID)
    cur = conn.cursor()

    try:
        # 1. Crea la tabella utenti se non esiste
        cur.execute("""
            CREATE TABLE IF NOT EXISTS utenti (
                id SERIAL PRIMARY KEY,
                azienda_id VARCHAR(50) NOT NULL,
                email VARCHAR(255) UNIQUE NOT NULL,
                password_hash VARCHAR(255) NOT NULL,
                ruolo VARCHAR(50) NOT NULL DEFAULT 'admin',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # 2. Crea la tabella audit_logs per il tracciamento della sicurezza
        cur.execute("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                id SERIAL PRIMARY KEY,
                azienda_id VARCHAR(50),
                livello VARCHAR(20),
                azione VARCHAR(255),
                dettagli TEXT,
                utente_email VARCHAR(255),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # 3. Verifica se l'utente esiste già
        cur.execute("SELECT id FROM utenti WHERE email = %s;", (EMAIL_TEST,))
        utente_esistente = cur.fetchone()

        if not utente_esistente:
            hashed_password = pwd_context.hash(PASSWORD_TEST)
            cur.execute("""
                INSERT INTO utenti (azienda_id, email, password_hash, ruolo)
                VALUES (%s, %s, %s, 'admin');
            """, (AZIENDA_ID, EMAIL_TEST, hashed_password))
            conn.commit()
            print(f"✅ Utente {EMAIL_TEST} e tabelle creati con successo!")
        else:
            print(f"ℹ️ Utente {EMAIL_TEST} già presente nel database.")

    except Exception as e:
        conn.rollback()
        print(f"❌ Errore durante l'inizializzazione del DB: {e}")
        raise e
    finally:
        cur.close()
        conn.close()

if __name__ == "__main__":
    inizializza_db_e_utente()