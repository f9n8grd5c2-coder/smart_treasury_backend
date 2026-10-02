import os
import psycopg2
from dotenv import load_dotenv

# Carica i dati dal .env
load_dotenv(override=True)

try:
    # 1. Ci colleghiamo al database
    conn = psycopg2.connect(
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT")
    )
    cur = conn.cursor()

    # 2. Diciamo al database di aggiungere la colonna 'livello' (testo massimo 50 caratteri)
    cur.execute("ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS livello VARCHAR(50);")
    conn.commit()

    print("✅ Colonna 'livello' aggiunta con successo alla tabella audit_logs!")

    cur.close()
    conn.close()

except Exception as e:
    print(f"Errore: {e}")