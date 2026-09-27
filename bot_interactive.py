import os
import time
import requests
from datetime import date, timedelta
from dotenv import load_dotenv
from db_helper import get_db_connection, decifra_dato, registra_audit_log

# Carica le variabili di ambiente dal file .env
load_dotenv()

# Legge i valori dal file .env in modo sicuro
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
AZIENDA_ID = os.getenv("AZIENDA_ID")


def invia_risposta(chat_id, testo):
    """Invia un messaggio di risposta all'utente su Telegram"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": testo, "parse_mode": "Markdown"}
    requests.post(url, json=payload)


def gestisci_comando_saldo(chat_id):
    """Risponde con i saldi di cassa aggiornati decifrando gli IBAN"""
    conn = get_db_connection(AZIENDA_ID)
    cur = conn.cursor()
    try:
        cur.execute("SELECT nome_istituto, iban, saldo_attuale FROM conti_bancari WHERE azienda_id = %s;",
                    (AZIENDA_ID,))
        conti = cur.fetchall()

        totale = sum(float(c[2]) for c in conti)
        testo = f"💳 *SITUAZIONE CONTI BANCARI*\n\n"
        for c in conti:
            iban_chiaro = decifra_dato(c[1])  # 🔓 Decifratura in memoria
            testo += f"• *{c[0]}* ({iban_chiaro[-4:]}): {float(c[2]):,.2f} €\n"
        testo += f"\n💰 *Totale Liquidità Disponibile:* {totale:,.2f} €"

        invia_risposta(chat_id, testo)
    finally:
        cur.close()
        conn.close()


def gestisci_comando_scadenze(chat_id):
    """Risponde con le scadenze B2B dei prossimi 15 giorni"""
    conn = get_db_connection(AZIENDA_ID)
    cur = conn.cursor()
    try:
        oggi = date.today()
        limite = oggi + timedelta(days=15)

        cur.execute("""
            SELECT s.denominazione, sc.data_scadenza, sc.importo_rata, sc.modalita_pagamento
            FROM scadenze_b2b sc
            JOIN fatture_b2b f ON sc.fattura_id = f.id
            JOIN soggetti_commerciali s ON f.soggetto_id = s.id
            WHERE f.azienda_id = %s AND sc.stato_pagamento = 'da_pagare'
            AND sc.data_scadenza BETWEEN %s AND %s
            ORDER BY sc.data_scadenza ASC;
        """, (AZIENDA_ID, oggi, limite))

        scadenze = cur.fetchall()
        if not scadenze:
            invia_risposta(chat_id, "🎉 Nessuna fattura in uscita nei prossimi 15 giorni!")
            return

        testo = "📅 *PROSSIME SCADENZE FATTURE (15 GG)*\n\n"
        totale = 0.0
        for sc in scadenze:
            imp = float(sc[2])
            totale += imp
            testo += f"• *{sc[1]}*: {sc[0]}\n  Importo: -{imp:,.2f} € ({sc[3]})\n"
        testo += f"\n🔴 *Totale da Pagare:* -{totale:,.2f} €"

        invia_risposta(chat_id, testo)
    finally:
        cur.close()
        conn.close()


def gestisci_comando_previsione(chat_id):
    """Calcola ed invia la stima di cassa a 30 giorni"""
    conn = get_db_connection(AZIENDA_ID)
    cur = conn.cursor()
    try:
        cur.execute("SELECT COALESCE(SUM(saldo_attuale), 0.00) FROM conti_bancari WHERE azienda_id = %s;",
                    (AZIENDA_ID,))
        saldo = float(cur.fetchone()[0])

        cur.execute("SELECT COALESCE(AVG(totale_giornaliero), 0.00) FROM corrispettivi_b2c WHERE azienda_id = %s;",
                    (AZIENDA_ID,))
        media_b2c = float(cur.fetchone()[0])

        estrazione_incassi_30g = media_b2c * 30

        cur.execute("""
            SELECT COALESCE(SUM(sc.importo_rata), 0.00)
            FROM scadenze_b2b sc
            JOIN fatture_b2b f ON sc.fattura_id = f.id
            WHERE f.azienda_id = %s AND sc.stato_pagamento = 'da_pagare'
            AND sc.data_scadenza BETWEEN %s AND %s;
        """, (AZIENDA_ID, date.today(), date.today() + timedelta(days=30)))
        uscite_30g = float(cur.fetchone()[0])

        saldo_30g = saldo + estrazione_incassi_30g - uscite_30g

        testo = f"""📈 *PREVISIONE CASSA A 30 GIORNI*

💰 Saldo Attuale: {saldo:,.2f} €
🛒 Incassi Banco Stimati (30g): +{estrazione_incassi_30g:,.2f} €
📄 Fatture in Scadenza (30g): -{uscite_30g:,.2f} €

🏁 *Saldo Stimato tra 30 giorni:* {saldo_30g:,.2f} €
"""
        invia_risposta(chat_id, testo)
    finally:
        cur.close()
        conn.close()


def ascolta_comandi_telegram():
    """Resta in ascolto dei comandi inviati dall'utente su Telegram"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    last_update_id = 0
    print("🤖 Assistente Virtuale in ascolto su Telegram... (Premi Ctrl+C per fermare)")

    while True:
        try:
            res = requests.get(url, params={"offset": last_update_id + 1, "timeout": 30}).json()
            for update in res.get("result", []):
                last_update_id = update["update_id"]
                message = update.get("message", {})
                testo_ricevuto = message.get("text", "").strip().lower()
                chat_id = message.get("chat", {}).get("id")

                if not chat_id:
                    continue

                if testo_ricevuto in ["/saldo", "saldo"]:
                    registra_audit_log(AZIENDA_ID, "INFO", "BOT_COMMAND", f"Richiesto /saldo da chat_id: {chat_id}")
                    gestisci_comando_saldo(chat_id)
                elif testo_ricevuto in ["/scadenze", "scadenze"]:
                    registra_audit_log(AZIENDA_ID, "INFO", "BOT_COMMAND", f"Richiesto /scadenze da chat_id: {chat_id}")
                    gestisci_comando_scadenze(chat_id)
                elif testo_ricevuto in ["/previsione", "previsione"]:
                    registra_audit_log(AZIENDA_ID, "INFO", "BOT_COMMAND",
                                       f"Richiesto /previsione da chat_id: {chat_id}")
                    gestisci_comando_previsione(chat_id)
                else:
                    invia_risposta(chat_id,
                                   "👋 Ciao! Inviami uno di questi comandi:\n\n• /saldo - Vedi la liquidità nei conti\n• /scadenze - Vedi le fatture da pagare\n• /previsione - Stima della cassa a 30 giorni")

        except Exception as e:
            time.sleep(2)


if __name__ == "__main__":
    ascolta_comandi_telegram()