-- ============================================================================
-- 1. ANAGRAFICA CLIENTI E STORICO AFFIDABILITÀ PAGAMENTI
-- ============================================================================
CREATE TABLE IF NOT EXISTS clienti (
    id SERIAL PRIMARY KEY,
    azienda_id VARCHAR(50) NOT NULL,
    ragione_sociale VARCHAR(255) NOT NULL,
    partita_iva VARCHAR(20),
    codice_fiscale VARCHAR(20),
    email_amministrazione VARCHAR(255),

    -- Metriche Storico Pagamenti
    ritardo_medio_giorni INT DEFAULT 0,            -- Giorni medi di ritardo rispetto alla scadenza
    fido_massimo_concesso NUMERIC(15, 2) DEFAULT 0.00, -- Limite di credito interno concesso al cliente
    percentuale_insoluti NUMERIC(5, 2) DEFAULT 0.00,  -- % di Ri.Ba. o assegni tornati insoluti
    bloccato_per_morosita BOOLEAN DEFAULT FALSE,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uk_piva_azienda UNIQUE (azienda_id, partita_iva)
);

-- ============================================================================
-- 2. ANAGRAFICA CONTI CORRENTI E CASSA
-- ============================================================================
CREATE TABLE IF NOT EXISTS conti_correnti (
    id SERIAL PRIMARY KEY,
    azienda_id VARCHAR(50) NOT NULL,
    nome_banca VARCHAR(100) NOT NULL,          -- es. Intesa Sanpaolo, Unicredit, Cassa di Risparmio
    filiale VARCHAR(100),
    iban VARCHAR(34) NOT NULL,
    bic_swift VARCHAR(11),
    tipologia VARCHAR(30) NOT NULL DEFAULT 'CONTO_CORRENTE', -- 'CONTO_CORRENTE', 'CASSA_CONTANTI', 'POS'
    valuta VARCHAR(3) DEFAULT 'EUR',
    saldo_contabile NUMERIC(15, 2) DEFAULT 0.00,  -- Saldo contabile reale ad oggi
    saldo_disponibile NUMERIC(15, 2) DEFAULT 0.00, -- Saldo contabile + Fidi accordati
    attivo BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uk_iban_azienda UNIQUE (azienda_id, iban)
);

-- ============================================================================
-- 3. LINEE DI CREDITO E FIDI (Scoperti di conto, Castelletto Ri.Ba. SBF)
-- ============================================================================
CREATE TABLE IF NOT EXISTS linee_credito (
    id SERIAL PRIMARY KEY,
    azienda_id VARCHAR(50) NOT NULL,
    conto_id INT NOT NULL REFERENCES conti_correnti(id) ON DELETE CASCADE,
    tipologia_linea VARCHAR(50) NOT NULL,
    -- 'SCOPERTO_CONTO' (Fido cassa), 'CASTELLETTO_RIBA_SBF', 'ANTICIPO_FATTURE'
    fido_accordato NUMERIC(15, 2) NOT NULL DEFAULT 0.00,
    fido_utilizzato NUMERIC(15, 2) NOT NULL DEFAULT 0.00,
    data_scadenza_fido DATE,
    note TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- 4. CONDIZIONI BANCA E COSTI OPERATIVI (Oneri e Valute)
-- ============================================================================
CREATE TABLE IF NOT EXISTS condizioni_bancarie (
    id SERIAL PRIMARY KEY,
    azienda_id VARCHAR(50) NOT NULL,
    conto_id INT NOT NULL REFERENCES conti_correnti(id) ON DELETE CASCADE,

    tasso_debitore_fido NUMERIC(5, 2) DEFAULT 0.00,       -- TAN fido accordato (%)
    tasso_sconfinamento NUMERIC(5, 2) DEFAULT 0.00,        -- TAN extra-fido (%)
    costo_singola_riba NUMERIC(6, 2) DEFAULT 0.00,        -- Costo presentazione Ri.Ba.
    costo_bonifico_sepa NUMERIC(6, 2) DEFAULT 0.00,       -- Costo bonifico in uscita
    costo_insoluto_riba NUMERIC(6, 2) DEFAULT 0.00,       -- Spesa per Ri.Ba. insoluta

    -- Giorni Valuta Medi Banca
    giorni_valuta_riba INT DEFAULT 3,                      -- Valuta SBF
    giorni_valuta_assegno INT DEFAULT 4,                   -- Valuta Assegno
    giorni_valuta_pos INT DEFAULT 1,                       -- Valuta POS

    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- 5. SCADENZARIO FLUSSI DI CASSA (Con Collegamento Cliente e Ritardo Stimato)
-- ============================================================================
CREATE TABLE IF NOT EXISTS flussi_finanziari (
    id SERIAL PRIMARY KEY,
    azienda_id VARCHAR(50) NOT NULL,
    conto_id INT REFERENCES conti_correnti(id) ON DELETE SET NULL,
    cliente_id INT REFERENCES clienti(id) ON DELETE SET NULL, -- Collegamento facoltativo all'anagrafica cliente

    tipo_flusso VARCHAR(10) NOT NULL,               -- 'INCASSO' o 'PAGAMENTO'
    ragione_sociale VARCHAR(255) NOT NULL,
    numero_documento VARCHAR(100),                   -- Nr. Fattura / Corrispettivo
    data_documento DATE NOT NULL,

    metodo_pagamento VARCHAR(30) NOT NULL,
    -- 'RIBA', 'BONIFICO', 'CONTANTI', 'ASSEGNO_BANCARIO', 'POS_BANCOMAT'

    importo_totale NUMERIC(15, 2) NOT NULL,
    data_scadenza DATE NOT NULL,                     -- Scadenza contrattuale
    ritardo_applicato_giorni INT DEFAULT 0,          -- Copiato dallo storico cliente al momento della creazione
    data_incasso_stimata DATE,                       -- Calcolata: data_scadenza + ritardo_applicato_giorni + giorni_valuta

    stato VARCHAR(30) NOT NULL DEFAULT 'IN_ATTESA',
    -- 'IN_ATTESA', 'PRESENTATO_SBF', 'INCASSATO', 'PAGATO', 'INSOLUTO'

    note TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- 6. INDICI DI PRESTAZIONE PER RICERCHE VELOCI
-- ============================================================================
CREATE INDEX IF NOT EXISTS idx_flussi_azienda_scadenza ON flussi_finanziari (azienda_id, data_scadenza, stato);
CREATE INDEX IF NOT EXISTS idx_clienti_azienda ON clienti (azienda_id, ragione_sociale);