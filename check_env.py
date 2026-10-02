import os
from dotenv import load_dotenv
import psycopg2

# Forza la lettura del .env sovrascrivendo eventuali variabili pre-esistenti
load_dotenv(override=True)

print("=== VERIFICA VARIABILI ===")
print(f"Database letto dal .env: '{os.getenv('DB_NAME')}'")
print(f"Utente letto dal .env: '{os.getenv('DB_USER')}'")

try:
    conn = psycopg2.connect(
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT")
    )
    cur = conn.cursor()

    # Chiediamo al database dove siamo finiti per davvero
    cur.execute("SELECT current_database();")
    db_reale = cur.fetchone()[0]

    # Chiediamo quali tabelle vede Python in questo database
    cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public';")
    tabelle = [t[0] for t in cur.fetchall()]

    print(f"\n=== VERIFICA DATABASE ===")
    print(f"Connesso realmente al database: '{db_reale}'")
    print(f"Tabelle che Python riesce a vedere qui dentro: {tabelle}")

except Exception as e:
    print(f"\nERRORE DI CONNESSIONE: {e}")