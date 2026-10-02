import os
import psycopg2
from dotenv import load_dotenv

load_dotenv(override=True)

try:
    conn = psycopg2.connect(
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT")
    )
    cur = conn.cursor()

    # 1. Rimuoviamo eventuali duplicati storici se la tabella ha righe sporche
    cur.execute(
        "DELETE FROM corrispettivi_b2c a USING corrispettivi_b2c b WHERE a.id > b.id AND a.azienda_id = b.azienda_id AND a.data_incasso = b.data_incasso;")

    # 2. Aggiungiamo il vincolo UNIQUE su (azienda_id, data_incasso) che serve per l'ON CONFLICT
    cur.execute("ALTER TABLE corrispettivi_b2c DROP CONSTRAINT IF EXISTS uk_azienda_data_corrispettivo;")
    cur.execute(
        "ALTER TABLE corrispettivi_b2c ADD CONSTRAINT uk_azienda_data_corrispettivo UNIQUE (azienda_id, data_incasso);")

    conn.commit()
    print("✅ Vincolo di unicità aggiunto con successo alla tabella corrispettivi_b2c!")

    cur.close()
    conn.close()

except Exception as e:
    print(f"Errore: {e}")