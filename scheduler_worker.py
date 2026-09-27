import os
import time
import schedule
from datetime import date, timedelta
from dotenv import load_dotenv
from db_helper import get_db_connection, registra_audit_log
from bot_notifier import invia_messaggio_telegram, genera_e_invia_report_settimanale

# Carica le variabili di ambiente
load_dotenv()

AZIENDA_ID = os.getenv("AZIENDA_ID")

def verifica_e_invia_alert_giornalieri():
    """
    Esegue la simulazione di cash flow ogni mattina.
    Se rileva uno scoperto di cassa nei prossimi 30 giorni, invia un alert urgente su Telegram.
    """
    print(f"⏰ [{time.strftime('%Y-%m-%d %H:%M:%S')}] Avvio controllo giornaliero liquidità...")
    registra_audit_log(AZIENDA_ID, "INFO", "WORKER_DAILY_CHECK", "Esecuzione controllo automatico cassa")

    conn = get_db_connection(AZIENDA_ID)
    cur = conn.cursor()

    try:
        # 1. Saldo iniziale
        cur.execute("SELECT COALESCE(SUM(saldo_attuale), 0.00) FROM conti_bancari WHERE azienda_id = %s;", (AZIENDA_ID,))
        saldo = float(cur.fetchone()[0])

        # 2. Media B2C
        cur.execute("""
            SELECT COALESCE(AVG(totale_giornaliero), 0.00) 
            FROM corrispettivi_b2c 
            WHERE azienda_id = %s AND data_corrispettivo >= %s;
        """, (AZIENDA_ID, date.today() - timedelta(days=30)))
        media_b2c = float(cur.fetchone()[0])

        # 3. Scadenze B2B
        cur.execute("""
            SELECT sc.data_scadenza, SUM(sc.importo_rata)
            FROM scadenze_b2b sc
            JOIN fatture_b2b f ON sc.fattura_id = f.id
            WHERE f.azienda_id = %s AND sc.stato_pagamento = 'da_pagare'
            GROUP BY sc.data_scadenza;
        """, (AZIENDA_ID,))
        scadenze_b2b = {row[0]: float(row[1]) for row in cur.fetchall()}

        # 4. Uscite Fisse
        cur.execute("""
            SELECT giorno_scadenza_mese, importo_stimato
            FROM uscite_ricorrenti
            WHERE azienda_id = %s AND attivo = TRUE;
        """, (AZIENDA_ID,))
        uscite_fisse = cur.fetchall()

        # Simulazione a 30 giorni
        saldo_progressivo = saldo
        data_corrente = date.today()
        alert_trovati = []

        for g in range(1, 31):
            giorno_simulato = data_corrente + timedelta(days=g)
            uscite_b2b = scadenze_b2b.get(giorno_simulato, 0.0)
            uscite_fisse_giorno = sum(float(u[1]) for u in uscite_fisse if u[0] == giorno_simulato.day)

            saldo_progressivo += media_b2c - (uscite_b2b + uscite_fisse_giorno)

            if saldo_progressivo < 0:
                alert_trovati.append((giorno_simulato, saldo_progressivo))

        # Se ci sono criticità, invia un alert immediato su Telegram
        if alert_trovati:
            primo_rosso = alert_trovati[0]
            messaggio_alert = f"""🚨 *ALERT CRITICITÀ CASSA*
🏢 *Ferramenta Test*

⚠️ Attenzione! La simulazione rileva un possibile *scoperto di cassa*.

📅 *Data Critica:* {primo_rosso[0]}
🔻 *Disponibilità Prevista:* {primo_rosso[1]:,.2f} €

Si consiglia di verificare i pagamenti B2B o pianificare un rientro di liquidità.
"""
            invia_messaggio_telegram(messaggio_alert)
            registra_audit_log(AZIENDA_ID, "WARNING", "ALERT_CASSA_INVIATO", f"Scoperto previsto per il {primo_rosso[0]}")
        else:
            print("✅ Controllo completato: Cassa regolare, nessun alert necessario.")

    except Exception as e:
        print(f"❌ Errore durante il controllo schedulato: {e}")
        registra_audit_log(AZIENDA_ID, "ERROR", "WORKER_ERROR", str(e))
    finally:
        cur.close()
        conn.close()

def esegui_report_settimanale():
    """Wrapper per l'invio del report settimanale"""
    print(f"⏰ [{time.strftime('%Y-%m-%d %H:%M:%S')}] Avvio invio report settimanale...")
    registra_audit_log(AZIENDA_ID, "INFO", "WORKER_WEEKLY_REPORT", "Invio report settimanale automatico")
    genera_e_invia_report_settimanale(AZIENDA_ID)

# ==============================================================================
# CONFIGURAZIONE PIANIFICAZIONE (SCHEDULER)
# ==============================================================================

# 1. Controllo quotidiano della cassa ogni mattina alle 08:00
schedule.every().day.at("08:00").do(verifica_e_invia_alert_giornalieri)

# 2. Report di sintesi settimanale ogni Lunedì alle 08:30
schedule.every().monday.at("08:30").do(esegui_report_settimanale)

if __name__ == "__main__":
    print("⚙️ Background Worker avviato e in esecuzione...")
    print("📅 Orari pianificati:")
    print("  • Tutti i giorni alle 08:00 -> Controllo Alert Cassa")
    print("  • Ogni Lunedì alle 08:30 -> Report Settimanale")
    print("   (Premi Ctrl+C per arrestare il worker)\n")

    # TEST IMMEDIATO ALL'AVVIO (Scommenta la riga sotto per testare subito l'esecuzione)
    # verifica_e_invia_alert_giornalieri()

    while True:
        schedule.run_pending()
        time.sleep(30)