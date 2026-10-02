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

    print("Costruzione tabelle in corso...")

    # 1. Tabella CONTI BANCARI
    cur.execute("""
        CREATE TABLE IF NOT EXISTS conti_bancari (
            id SERIAL PRIMARY KEY,
            azienda_id VARCHAR(50) NOT NULL,
            nome_istituto VARCHAR(100) NOT NULL,
            iban VARCHAR(255) NOT NULL,
            saldo_attuale NUMERIC(15, 2) DEFAULT 0.00
        );
    """)

    # 2. Tabella CORRISPETTIVI B2C (Incassi del banco)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS corrispettivi_b2c (
            id SERIAL PRIMARY KEY,
            azienda_id VARCHAR(50) NOT NULL,
            data_corrispettivo DATE NOT NULL,
            contanti NUMERIC(10, 2) DEFAULT 0.00,
            pos NUMERIC(10, 2) DEFAULT 0.00,
            totale_giornaliero NUMERIC(10, 2) DEFAULT 0.00,
            note TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # 3. Tabella FATTURE B2B (Acquisti/Vendite)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS fatture_b2b (
            id SERIAL PRIMARY KEY,
            azienda_id VARCHAR(50) NOT NULL,
            fornitore_id INTEGER REFERENCES fornitori(id) ON DELETE SET NULL,
            numero_documento VARCHAR(50) NOT NULL,
            data_emissione DATE NOT NULL,
            importo_totale NUMERIC(12, 2) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # 4. Tabella SCADENZE B2B (Rate da pagare/incassare)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS scadenze_b2b (
            id SERIAL PRIMARY KEY,
            fattura_id INTEGER REFERENCES fatture_b2b(id) ON DELETE CASCADE,
            data_scadenza DATE NOT NULL,
            importo_rata NUMERIC(12, 2) NOT NULL,
            stato_pagamento VARCHAR(20) DEFAULT 'da_pagare', -- 'da_pagare', 'pagato', 'scaduto'
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # 5. Tabella USCITE RICORRENTI (Es. affitto, stipendi)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS uscite_ricorrenti (
            id SERIAL PRIMARY KEY,
            azienda_id VARCHAR(50) NOT NULL,
            descrizione VARCHAR(100) NOT NULL,
            giorno_scadenza_mese INTEGER NOT NULL CHECK (giorno_scadenza_mese BETWEEN 1 AND 31),
            importo_stimato NUMERIC(10, 2) NOT NULL,
            attivo BOOLEAN DEFAULT TRUE
        );
    """)

    conn.commit()
    print("✅ Tutte le 5 tabelle del Cash Flow sono state create con successo!")

    cur.close()
    conn.close()

except Exception as e:
    print(f"Errore durante la creazione delle tabelle: {e}")