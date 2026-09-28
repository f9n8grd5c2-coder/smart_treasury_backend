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
        # 1. Tabella utenti
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

        # 2. Tabella audit_logs
        cur.execute("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                id SERIAL PRIMARY KEY,
                azienda_id VARCHAR(50),
                livello VARCHAR(20),
                azione VARCHAR(255),
                dettagli TEXT,
                utente_email VARCHAR(255),
                indirizzo_ip VARCHAR(50),
                user_agent TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # 3. Tabella conti_correnti per l'endpoint /conti/saldo
        cur.execute("""
            CREATE TABLE IF NOT EXISTS conti_correnti (
                id SERIAL PRIMARY KEY,
                azienda_id VARCHAR(50) NOT NULL,
                nome_banca VARCHAR(100) NOT NULL,
                iban VARCHAR(34) NOT NULL,
                saldo_contabile NUMERIC(15, 2) DEFAULT 0.00,
                saldo_disponibile NUMERIC(15, 2) DEFAULT 0.00,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # 4. Inserimento utente di test
        cur.execute("SELECT id FROM utenti WHERE email = %s;", (EMAIL_TEST,))
        if not cur.fetchone():
            hashed_password = pwd_context.hash(PASSWORD_TEST)
            cur.execute("""
                INSERT INTO utenti (azienda_id, email, password_hash, ruolo)
                VALUES (%s, %s, %s, 'admin');
            """, (AZIENDA_ID, EMAIL_TEST, hashed_password))

        # 5. Inserimento conto di prova (se non presente)
        cur.execute("SELECT id FROM conti_correnti WHERE azienda_id = %s;", (AZIENDA_ID,))
        if not cur.fetchone():
            cur.execute("""
                INSERT INTO conti_correnti (azienda_id, nome_banca, iban, saldo_contabile, saldo_disponibile)
                VALUES (%s, 'Banca Intesa Test', 'IT60X0542811101000000123456', 15000.00, 15000.00);
            """, (AZIENDA_ID,))

        conn.commit()
        print("✅ Schema DB, utente di test e conto corrente inizializzati con successo!")

    except Exception as e:
        conn.rollback()
        print(f"❌ Errore durante l'inizializzazione del DB: {e}")
        raise e
    finally:
        cur.close()
        conn.close()

if __name__ == "__main__":
    inizializza_db_e_utente()