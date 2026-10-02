import os
import psycopg2
from db_helper import get_db_connection

AZIENDA_ID = os.getenv("AZIENDA_ID", "az_001_ferramenta")


def esegui_schema_sql():
    print("🚀 Avvio creazione/aggiornamento tabelle database...")

    path_schema = os.path.join(os.path.dirname(__file__), "schema.sql")
    if not os.path.exists(path_schema):
        print("❌ Errore: File schema.sql non trovato!")
        return

    with open(path_schema, "r", encoding="utf-8") as f:
        sql_script = f.read()

    try:
        conn = get_db_connection(AZIENDA_ID)
        # Mostra esattamente quale DB e utente si stanno usando
        info = conn.get_dsn_parameters()
        print(f"🔌 Connesso al DB: '{info.get('dbname')}' come utente: '{info.get('user')}'")

        cur = conn.cursor()
        cur.execute(sql_script)
        conn.commit()
        print("✅ Schema del database aggiornato con successo!")
    except Exception as e:
        print(f"❌ Errore durante l'esecuzione dello schema SQL: {e}")
    finally:
        if 'cur' in locals():
            cur.close()
        if 'conn' in locals():
            conn.close()


if __name__ == "__main__":
    esegui_schema_sql()