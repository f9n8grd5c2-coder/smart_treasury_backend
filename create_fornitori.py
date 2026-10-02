import os
import psycopg2
from dotenv import load_dotenv

load_dotenv(override=True)

try:
    # Ci colleghiamo al database
    conn = psycopg2.connect(
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT")
    )
    cur = conn.cursor()

    # Diciamo al database di costruire la tabella fornitori con tutte le colonne necessarie
    cur.execute("""
        CREATE TABLE IF NOT EXISTS fornitori (
            id SERIAL PRIMARY KEY,
            azienda_id VARCHAR(50) NOT NULL,
            ragione_sociale VARCHAR(255) NOT NULL,
            partita_iva VARCHAR(20) NOT NULL,
            codice_fiscale VARCHAR(20),
            email_amministrazione VARCHAR(255),
            iban VARCHAR(50),
            giorni_pagamento_standard INTEGER DEFAULT 30,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()

    print("✅ Tabella 'fornitori' creata con successo nel database Docker!")

    cur.close()
    conn.close()

except Exception as e:
    print(f"Errore: {e}")