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

    # 1. Tabella Clienti (se non esiste)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS clienti (
            id SERIAL PRIMARY KEY,
            azienda_id VARCHAR(100) NOT NULL,
            ragione_sociale VARCHAR(255) NOT NULL,
            partita_iva VARCHAR(20),
            codice_fiscale VARCHAR(20),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # 2. Tabella Fatture Attive (verso i clienti)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS fatture_attive (
            id SERIAL PRIMARY KEY,
            azienda_id VARCHAR(100) NOT NULL,
            cliente_id INTEGER REFERENCES clienti(id),
            numero_documento VARCHAR(50) NOT NULL,
            data_emissione DATE NOT NULL,
            importo_totale NUMERIC(12,2) NOT NULL,
            stato_incasso VARCHAR(30) DEFAULT 'da_incassare',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    conn.commit()
    print("✅ Tabelle per il Ciclo Attivo (Clienti e Fatture Attive) create con successo!")

    cur.close()
    conn.close()

except Exception as e:
    print(f"Errore durante la creazione delle tabelle: {e}")