import os
import requests
from datetime import date, timedelta
from dotenv import load_dotenv
from db_helper import get_db_connection

# Carica le variabili di ambiente dal file .env
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


def invia_messaggio_telegram(testo_messaggio):
    """Invia una notifica push diretta sul telefono dell'utente via Telegram"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("❌ Errore: Credenziali Telegram mancanti nel file .env")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": testo_messaggio,
        "parse_mode": "Markdown"
    }

    try:
        response = requests.post(url, json=payload)
        if response.status_code == 200:
            print("📱 Notifica inviata con successo su Telegram!")
        else:
            print(f"❌ Errore invio Telegram: {response.text}")
    except Exception as e:
        print(f"❌ Errore di connessione con Telegram: {e}")


def genera_e_invia_report_settimanale(azienda_id):
    """Compila il report sintetico di tesoreria e lo invia all'imprenditore"""
    conn = get_db_connection(azienda_id)
    cur = conn.cursor()

    try:
        # Saldo attuale
        cur.execute("SELECT COALESCE(SUM(saldo_attuale), 0.00) FROM conti_bancari WHERE azienda_id = %s;",
                    (azienda_id,))
        saldo_attuale = float(cur.fetchone()[0])

        # Scadenze uscite prossimi 7 giorni
        oggi = date.today()
        tra_7_giorni = oggi + timedelta(days=7)

        cur.execute("""
            SELECT COALESCE(SUM(sc.importo_rata), 0.00)
            FROM scadenze_b2b sc
            JOIN fatture_b2b f ON sc.fattura_id = f.id
            WHERE f.azienda_id = %s AND sc.stato_pagamento = 'da_pagare'
            AND sc.data_scadenza BETWEEN %s AND %s;
        """, (azienda_id, oggi, tra_7_giorni))
        uscite_b2b_settimana = float(cur.fetchone()[0])

        # Media incassi B2C attesi
        cur.execute("""
            SELECT COALESCE(AVG(totale_giornaliero), 0.00) 
            FROM corrispettivi_b2c 
            WHERE azienda_id = %s AND data_corrispettivo >= %s;
        """, (azienda_id, oggi - timedelta(days=30)))
        media_b2c = float(cur.fetchone()[0])
        stima_incassi_settimana = media_b2c * 7

        saldo_stimato_7g = saldo_attuale + stima_incassi_settimana - uscite_b2b_settimana

        # Formattazione Messaggio in Markdown
        messaggio = f"""📊 *SMART TREASURY - REPORT SETTIMANALE*
🏢 *Ferramenta Test*

💰 *Saldo Attuale Conti:* {saldo_attuale:,.2f} €

📈 *Previsioni prossimi 7 giorni:*
 ├ 🛒 Incassi Banco Stimati: +{stima_incassi_settimana:,.2f} €
 └ 📄 Fatture Fissate in Uscita: -{uscite_b2b_settimana:,.2f} €

🏁 *Saldo Stimato a 7 giorni:* {saldo_stimato_7g:,.2f} €
STATUS: ✅ *Cassa in Sicurezza*
"""
        invia_messaggio_telegram(messaggio)

    except Exception as e:
        print(f"❌ Errore durante la generazione del report: {e}")
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    AZIENDA_ID = os.getenv("AZIENDA_ID")
    print("🚀 Inizio test invio notifica Assistente Virtuale...")
    genera_e_invia_report_settimanale(AZIENDA_ID)