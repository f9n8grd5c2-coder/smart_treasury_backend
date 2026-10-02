import os
from passlib.context import CryptContext
from db_helper import get_db_connection

# Strumento per criptare la password in modo sicuro
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def prepara_db_locale():
    print("⚙️ Inizio configurazione del database locale...")
    azienda_id = os.getenv("AZIENDA_ID", "az_001_ferramenta")

    conn = get_db_connection(azienda_id)
    cur = conn.cursor()

    try:
        # 1. Creiamo le tabelle di sistema che mancano in locale
        print("Creazione tabelle utenti e audit_logs...")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS utenti (
                id SERIAL PRIMARY KEY,
                azienda_id VARCHAR(50) NOT NULL,
                email VARCHAR(255) UNIQUE NOT NULL,
                password_hash VARCHAR(255) NOT NULL,
                ruolo VARCHAR(50) DEFAULT 'admin'
            );

            CREATE TABLE IF NOT EXISTS audit_logs (
                id SERIAL PRIMARY KEY,
                azienda_id VARCHAR(50),
                categoria VARCHAR(50),
                azione VARCHAR(50),
                dettagli TEXT,
                data_ora TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # 2. Creiamo il tuo utente per farti entrare nell'app
        email_admin = "admin@ferramenta.it"
        password_chiara = "PasswordSicura123!"
        password_hashata = pwd_context.hash(password_chiara)

        # Controlla se l'utente esiste già
        cur.execute("SELECT id FROM utenti WHERE email = %s", (email_admin,))
        if not cur.fetchone():
            cur.execute("""
                INSERT INTO utenti (azienda_id, email, password_hash, ruolo)
                VALUES (%s, %s, %s, 'admin')
            """, (azienda_id, email_admin, password_hashata))
            print(f"👤 Utente creato: {email_admin} / {password_chiara}")

        conn.commit()
        print("✅ Database locale configurato con successo. Pronto per i test!")

    except Exception as e:
        conn.rollback()
        print(f"❌ Errore durante la configurazione: {e}")
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    prepara_db_locale()