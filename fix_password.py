import os
import psycopg2
from passlib.context import CryptContext
from dotenv import load_dotenv

load_dotenv(override=True)

# 1. Generiamo un vero hash crittografato per la password "admin123"
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
password_criptata = pwd_context.hash("admin123")

try:
    conn = psycopg2.connect(
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT")
    )
    cur = conn.cursor()

    # 2. Proviamo ad aggiornare la colonna (di solito si chiama 'password' o 'password_hash')
    try:
        cur.execute("UPDATE utenti SET password = %s WHERE email = 'admin@ferramenta.it';", (password_criptata,))
        conn.commit()
    except psycopg2.errors.UndefinedColumn:
        conn.rollback()
        # Se la colonna si chiama diversamente, usiamo password_hash
        cur.execute("UPDATE utenti SET password_hash = %s WHERE email = 'admin@ferramenta.it';", (password_criptata,))
        conn.commit()

    print("✅ Successo! La password di admin@ferramenta.it è stata crittografata correttamente.")

except Exception as e:
    print(f"Errore: {e}")