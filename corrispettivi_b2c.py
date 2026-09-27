import os
from datetime import date, timedelta
from dotenv import load_dotenv
from db_helper import get_db_connection

# Carica le variabili di ambiente dal file .env
load_dotenv()


def inserisci_corrispettivo_giornaliero(azienda_id, data_incasso, contanti, pos, note=""):
    """Registra l'incasso quotidiano da banco (B2C) nel database in modo sicuro"""
    conn = get_db_connection(azienda_id)
    cur = conn.cursor()

    try:
        cur.execute("""
            INSERT INTO corrispettivi_b2c (azienda_id, data_corrispettivo, incasso_contanti, incasso_pos, note)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id, totale_giornaliero;
        """, (azienda_id, data_incasso, contanti, pos, note))

        res = cur.fetchone()
        conn.commit()
        print(f"✅ Incasso B2C del {data_incasso} registrato in sicurezza! TOTALE = {res[1]}€")

    except Exception as e:
        conn.rollback()
        print(f"❌ Errore durante il salvataggio del corrispettivo: {e}")
    finally:
        cur.close()
        conn.close()


def calcola_media_incassi_b2c(azienda_id, giorni_analisi=30):
    """Calcola la media degli incassi giornalieri storici"""
    conn = get_db_connection(azienda_id)
    cur = conn.cursor()

    try:
        data_inizio = date.today() - timedelta(days=giorni_analisi)

        cur.execute("""
            SELECT 
                ROUND(AVG(totale_giornaliero), 2) AS media_giornaliera,
                ROUND(AVG(incasso_contanti), 2) AS media_contanti,
                ROUND(AVG(incasso_pos), 2) AS media_pos
            FROM corrispettivi_b2c
            WHERE azienda_id = %s AND data_corrispettivo >= %s;
        """, (azienda_id, data_inizio))

        stats = cur.fetchone()
        media_totale = stats[0] if stats[0] is not None else 0.00

        print(f"\n📊 Media Giornaliera B2C: {media_totale} €")
        return media_totale

    except Exception as e:
        print(f"❌ Errore durante il calcolo delle medie: {e}")
        return 0.00
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    AZIENDA_ID = os.getenv("AZIENDA_ID")
    calcola_media_incassi_b2c(AZIENDA_ID)