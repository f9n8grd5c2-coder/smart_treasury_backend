from db_helper import get_db_connection

print("Connessione al database in corso...")
conn = get_db_connection()
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS clienti (
    id SERIAL PRIMARY KEY,
    azienda_id VARCHAR(50) NOT NULL,
    ragione_sociale VARCHAR(255) NOT NULL,
    partita_iva VARCHAR(20),
    codice_fiscale VARCHAR(20),
    email_amministrazione VARCHAR(255),
    ritardo_medio_giorni INTEGER DEFAULT 0,
    fido_massimo_concesso DECIMAL(10,2) DEFAULT 0.00,
    data_creazione TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
""")
conn.commit()
print("Successo! Tabella 'clienti' creata esattamente dove FastAPI la sta cercando.")

cur.close()
conn.close()