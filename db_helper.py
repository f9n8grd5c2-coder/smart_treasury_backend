import os
import psycopg2
from dotenv import load_dotenv
from cryptography.fernet import Fernet

# Carica le variabili di ambiente dal file .env
load_dotenv()

# Inizializza la cifra Fernet (AES-256) se presente la chiave
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")
cipher = Fernet(ENCRYPTION_KEY.encode()) if ENCRYPTION_KEY else None


def cifra_dato(testo_in_chiaro: str) -> str:
    """Cifra una stringa (es. IBAN) restituendo la versione cifrata AES-256"""
    if not testo_in_chiaro or not cipher:
        return testo_in_chiaro
    return cipher.encrypt(testo_in_chiaro.encode()).decode()


def decifra_dato(testo_cifrato: str) -> str:
    """Decifra un dato salvato nel DB riportandolo in testo chiaro"""
    if not testo_cifrato or not cipher:
        return testo_cifrato
    try:
        return cipher.decrypt(testo_cifrato.encode()).decode()
    except Exception:
        # Se il dato nel DB era già in chiaro, restituisce l'originale
        return testo_cifrato


def get_db_connection(azienda_id=None):
    """Apre una connessione a PostgreSQL leggendo i parametri dal file .env con RLS"""
    conn = psycopg2.connect(
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT")
    )

    if azienda_id:
        cur = conn.cursor()
        cur.execute("SET LOCAL app.current_azienda_id = %s;", (azienda_id,))
        cur.close()

    return conn
def registra_audit_log(azienda_id, livello, azione, dettagli="", ip="127.0.0.1"):
    """Registra un evento di sicurezza o di sistema nella tabella audit_logs"""
    try:
        conn = psycopg2.connect(
            dbname=os.getenv("DB_NAME"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
            host=os.getenv("DB_HOST"),
            port=os.getenv("DB_PORT")
        )
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO audit_logs (azienda_id, livello, azione, dettagli, indirizzo_ip)
            VALUES (%s, %s, %s, %s, %s);
        """, (azienda_id, livello, azione, dettagli, ip))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"⚠️ Impossibile scrivere l'audit log: {e}")