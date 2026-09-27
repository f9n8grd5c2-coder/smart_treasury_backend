import os
from datetime import date, timedelta
from dotenv import load_dotenv
from db_helper import get_db_connection, cifra_dato

# Carica le variabili di ambiente dal file .env
load_dotenv()


def imposta_saldo_banca_iniziale(azienda_id, nome_banca, iban, saldo, fido=0.0):
    """Configura il conto corrente cifrando l'IBAN per sicurezza at-rest"""
    conn = get_db_connection(azienda_id)
    cur = conn.cursor()
    try:
        iban_cifrato = cifra_dato(iban)  # 🔒 Cifratura AES-256
        cur.execute("""
            INSERT INTO conti_bancari (azienda_id, nome_istituto, iban, saldo_attuale, fido_accordato)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING;
        """, (azienda_id, nome_banca, iban_cifrato, saldo, fido))
        conn.commit()
    finally:
        cur.close()
        conn.close()


def inserisci_uscita_ricorrente(azienda_id, categoria, descrizione, importo, giorno_scadenza):
    """Registra costi fissi ricorrenti come F24, stipendi, affitto, ecc."""
    conn = get_db_connection(azienda_id)
    cur = conn.cursor()
    try:
        prossima = date(date.today().year, date.today().month, giorno_scadenza)
        if prossima < date.today():
            mese = date.today().month + 1 if date.today().month < 12 else 1
            anno = date.today().year if date.today().month < 12 else date.today().year + 1
            prossima = date(anno, mese, giorno_scadenza)

        cur.execute("""
            INSERT INTO uscite_ricorrenti (azienda_id, categoria, descrizione, importo_stimato, frequenza, giorno_scadenza_mese, prossima_scadenza)
            VALUES (%s, %s, %s, %s, 'mensile', %s, %s);
        """, (azienda_id, categoria, descrizione, importo, giorno_scadenza, prossima))
        conn.commit()
    finally:
        cur.close()
        conn.close()


def calcola_previsione_cashflow(azienda_id, giorni_previsione=30):
    """
    MOTORE PRINCIPALE: Simula l'andamento della liquidità giorno per giorno
    """
    conn = get_db_connection(azienda_id)
    cur = conn.cursor()

    try:
        # 1. Recupera il Saldo Attuale Totale dai conti bancari
        cur.execute("SELECT COALESCE(SUM(saldo_attuale), 0.00) FROM conti_bancari WHERE azienda_id = %s;",
                    (azienda_id,))
        saldo_iniziale = float(cur.fetchone()[0])

        # 2. Recupera la media degli incassi giornalieri B2C
        data_inizio_media = date.today() - timedelta(days=30)
        cur.execute("""
            SELECT COALESCE(AVG(totale_giornaliero), 0.00) 
            FROM corrispettivi_b2c 
            WHERE azienda_id = %s AND data_corrispettivo >= %s;
        """, (azienda_id, data_inizio_media))
        media_incasso_b2c = float(cur.fetchone()[0])

        # 3. Recupera tutte le scadenze B2B da pagare
        cur.execute("""
            SELECT sc.data_scadenza, SUM(sc.importo_rata)
            FROM scadenze_b2b sc
            JOIN fatture_b2b f ON sc.fattura_id = f.id
            WHERE f.azienda_id = %s AND sc.stato_pagamento = 'da_pagare'
            GROUP BY sc.data_scadenza;
        """, (azienda_id,))
        scadenze_b2b = {row[0]: float(row[1]) for row in cur.fetchall()}

        # 4. Recupera le uscite fisse mensili
        cur.execute("""
            SELECT giorno_scadenza_mese, importo_stimato, descrizione
            FROM uscite_ricorrenti
            WHERE azienda_id = %s AND attivo = TRUE;
        """, (azienda_id,))
        uscite_fisse = cur.fetchall()

        # SIMULAZIONE GIORNO PER GIORNO
        saldo_progressivo = saldo_iniziale
        data_corrente = date.today()
        alert_trovati = []

        print("\n==================================================================")
        print(f"📈 SIMULAZIONE PREVISIONALE CASH-FLOW PROSSIMI {giorni_previsione} GIORNI")
        print(f"💰 Saldo Iniziale di Cassa: {saldo_iniziale:.2f} €")
        print(f"🛒 Stima Incasso Giornaliero Banco (B2C): +{media_incasso_b2c:.2f} €/giorno")
        print("==================================================================\n")

        for g in range(1, giorni_previsione + 1):
            giorno_simulato = data_corrente + timedelta(days=g)

            entrate_giorno = media_incasso_b2c
            uscite_b2b = scadenze_b2b.get(giorno_simulato, 0.0)

            uscite_fisse_giorno = 0.0
            dettaglio_uscite_fisse = []
            for u in uscite_fisse:
                if u[0] == giorno_simulato.day:
                    uscite_fisse_giorno += float(u[1])
                    dettaglio_uscite_fisse.append(f"{u[2]} ({u[1]}€)")

            uscite_totali_giorno = uscite_b2b + uscite_fisse_giorno
            saldo_progressivo = saldo_progressivo + entrate_giorno - uscite_totali_giorno

            if saldo_progressivo < 0:
                alert_trovati.append((giorno_simulato, saldo_progressivo))
                flag_status = "⚠️ RED / SCOPERTO"
            elif saldo_progressivo < 1000:
                flag_status = "⚡ SOGLIA CRITICA"
            else:
                flag_status = "✅ OK"

            if uscite_totali_giorno > 0 or g % 7 == 0:
                note_uscita = ""
                if uscite_b2b > 0:
                    note_uscita += f" [Scad. Fattura B2B: -{uscite_b2b:.2f}€]"
                if uscite_fisse_giorno > 0:
                    note_uscita += f" [Uscita Fissa: {', '.join(dettaglio_uscite_fisse)}]"

                print(
                    f"Data: {giorno_simulato} | Saldo Previsto: {saldo_progressivo:10.2f} € | Status: {flag_status}{note_uscita}")

        print("\n------------------------------------------------------------------")
        if alert_trovati:
            print("🚨 CRITICITÀ DI CASSA RILEVATE:")
            for alt in alert_trovati:
                print(f"  ❌ In data {alt[0]} la cassa andrà in rosso di {alt[1]:.2f} €!")
        else:
            print("🎉 NESSUN RISCHIO DI SCOPERTO: La cassa rimane positiva nei prossimi giorni.")
        print("------------------------------------------------------------------\n")

    except Exception as e:
        print(f"❌ Errore durante la simulazione di cashflow: {e}")
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    AZIENDA_ID = os.getenv("AZIENDA_ID")

    # 1. Impostiamo il saldo bancario di partenza della Ferramenta
    imposta_saldo_banca_iniziale(AZIENDA_ID, "Intesa Sanpaolo", "IT99A0000000000000000000000", saldo=2500.00,
                                 fido=3000.00)

    # 2. Impostiamo le uscite ricorrenti fisse
    inserisci_uscita_ricorrente(AZIENDA_ID, "stipendi", "Stipendio Commesso", importo=1500.00, giorno_scadenza=27)
    inserisci_uscita_ricorrente(AZIENDA_ID, "f24_tasse", "F24 IVA e Contributi", importo=1800.00, giorno_scadenza=16)

    # 3. AVVIAMO LA SIMULAZIONE PREVISIONALE A 45 GIORNI
    calcola_previsione_cashflow(AZIENDA_ID, giorni_previsione=45)