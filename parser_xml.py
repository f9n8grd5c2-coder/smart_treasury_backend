import os
import defusedxml.ElementTree as ET
from datetime import datetime
from dotenv import load_dotenv
from db_helper import get_db_connection

# Carica le variabili di ambiente dal file .env
load_dotenv()


def rimuovi_namespace(tree):
    """Rimuove tutti i prefissi di namespace dai tag XML per una lettura universale"""
    for elem in tree.iter():
        if '}' in elem.tag:
            elem.tag = elem.tag.split('}', 1)[1]


def parse_fattura_xml(xml_path):
    """
    Legge ed estrae i dati chiave da qualsiasi XML di Fattura Elettronica SDI
    """
    tree = ET.parse(xml_path)
    rimuovi_namespace(tree)
    root = tree.getroot()

    header = root.find("FatturaElettronicaHeader")
    body = root.find("FatturaElettronicaBody")

    if header is None or body is None:
        raise ValueError("Struttura XML non valida per Fattura Elettronica SDI")

    # 1. Dati del Fornitore (Cedente/Prestatore)
    cedente = header.find("CedentePrestatore/DatiAnagrafici")
    piva_fornitore = ""
    denominazione_fornitore = ""

    if cedente is not None:
        piva_elem = cedente.find("IdFiscaleIVA/IdCodice")
        piva_fornitore = piva_elem.text.strip() if piva_elem is not None and piva_elem.text else ""

        denom_elem = cedente.find("Anagrafica/Denominazione")
        if denom_elem is not None and denom_elem.text:
            denominazione_fornitore = denom_elem.text.strip()
        else:
            nome_elem = cedente.find("Anagrafica/Nome")
            cognome_elem = cedente.find("Anagrafica/Cognome")
            nome = nome_elem.text.strip() if nome_elem is not None and nome_elem.text else ""
            cognome = cognome_elem.text.strip() if cognome_elem is not None and cognome_elem.text else ""
            denominazione_fornitore = f"{nome} {cognome}".strip()

    # 2. Dati Generali della Fattura
    dati_generali = body.find("DatiGenerali/DatiGeneraliDocumento")
    numero_doc = dati_generali.find("Numero").text.strip() if dati_generali is not None and dati_generali.find(
        "Numero") is not None else "ND"

    data_elem = dati_generali.find("Data") if dati_generali is not None else None
    data_doc_str = data_elem.text.strip() if data_elem is not None and data_elem.text else "2026-01-01"
    data_documento = datetime.strptime(data_doc_str, "%Y-%m-%d").date()

    totale_elem = dati_generali.find("ImportoTotaleDocumento") if dati_generali is not None else None
    importo_totale = float(totale_elem.text.strip()) if totale_elem is not None and totale_elem.text else 0.0

    # Calcolo Imponibile e IVA
    dati_riepilogo = body.findall("DatiBeniServizi/DatiRiepilogo")
    importo_imponibile = 0.0
    importo_iva = 0.0
    for r in dati_riepilogo:
        imp_elem = r.find("ImponibileImporto")
        iva_elem = r.find("Imposta")
        if imp_elem is not None and imp_elem.text:
            importo_imponibile += float(imp_elem.text.strip())
        if iva_elem is not None and iva_elem.text:
            importo_iva += float(iva_elem.text.strip())

    # 3. Scadenze di Pagamento
    scadenze = []
    dati_pagamento = body.findall("DatiPagamento")
    for pag in dati_pagamento:
        dettagli = pag.findall("DettaglioPagamento")
        for dett in dettagli:
            mod_elem = dett.find("ModalitaPagamento")
            modalita = mod_elem.text.strip().lower() if mod_elem is not None and mod_elem.text else "bonifico"

            scad_elem = dett.find("DataScadenzaPagamento")
            scad_str = scad_elem.text.strip() if scad_elem is not None and scad_elem.text else data_doc_str
            data_scad = datetime.strptime(scad_str, "%Y-%m-%d").date()

            imp_elem = dett.find("ImportoPagamento")
            imp_rata = float(imp_elem.text.strip()) if imp_elem is not None and imp_elem.text else importo_totale

            scadenze.append({
                "data_scadenza": data_scad,
                "importo_rata": imp_rata,
                "modalita_pagamento": modalita
            })

    if not scadenze:
        scadenze.append({
            "data_scadenza": data_documento,
            "importo_rata": importo_totale,
            "modalita_pagamento": "bonifico"
        })

    return {
        "piva_fornitore": piva_fornitore,
        "denominazione_fornitore": denominazione_fornitore,
        "numero_documento": numero_doc,
        "data_documento": data_documento,
        "importo_totale": importo_totale,
        "importo_imponibile": importo_imponibile,
        "importo_iva": importo_iva,
        "scadenze": scadenze
    }


def salva_fattura_in_db(azienda_id, fattura_data):
    """Salva la fattura e le sue scadenze nel database PostgreSQL usando la tabella fornitori"""
    conn = get_db_connection(azienda_id)
    cur = conn.cursor()

    try:
        # A. Inserisci o recupera il Fornitore dalla tabella 'fornitori'
        cur.execute("""
            INSERT INTO fornitori (azienda_id, ragione_sociale, partita_iva)
            VALUES (%s, %s, %s)
            ON CONFLICT DO NOTHING;
        """, (azienda_id, fattura_data["denominazione_fornitore"], fattura_data["piva_fornitore"]))

        # Recuperiamo l'ID del fornitore
        cur.execute("SELECT id FROM fornitori WHERE partita_iva = %s AND azienda_id = %s;",
                    (fattura_data["piva_fornitore"], azienda_id))
        fornitore_res = cur.fetchone()
        fornitore_id = fornitore_res[0] if fornitore_res else None

        # B. Inserisci la Fattura B2B nella tabella 'fatture_b2b'
        cur.execute("""
            INSERT INTO fatture_b2b (azienda_id, fornitore_id, numero_documento, data_emissione, importo_totale)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id;
        """, (
            azienda_id,
            fornitore_id,
            fattura_data["numero_documento"],
            fattura_data["data_documento"],
            fattura_data["importo_totale"]
        ))
        fattura_id = cur.fetchone()[0]

        # C. Inserisci le Scadenze nella tabella 'scadenze_b2b'
        for scad in fattura_data["scadenze"]:
            cur.execute("""
                INSERT INTO scadenze_b2b (fattura_id, data_scadenza, importo_rata, stato_pagamento)
                VALUES (%s, %s, %s, 'da_pagare');
            """, (
                fattura_id,
                scad["data_scadenza"],
                scad["importo_rata"]
            ))

        conn.commit()
        print(
            f"✅ Fattura N. {fattura_data['numero_documento']} di '{fattura_data['denominazione_fornitore']}' (Totale: {fattura_data['importo_totale']} €) salvata e scadenze generate con successo!")

    except Exception as e:
        conn.rollback()
        print(f"❌ Errore durante il salvataggio nel DB: {e}")
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    AZIENDA_ID = os.getenv("AZIENDA_ID")
    percorso_file_xml = "inbox_xml/fattura_prova.xml"

    print("📄 Analisi file XML in corso...")
    try:
        dati_fattura = parse_fattura_xml(percorso_file_xml)
        print("💾 Salvataggio nel database PostgreSQL...")
        salva_fattura_in_db(AZIENDA_ID, dati_fattura)
    except Exception as e:
        print(f"❌ Impossibile completare il test: {e}")