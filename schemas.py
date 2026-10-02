from pydantic import BaseModel, EmailStr, Field
from typing import Optional, List
from datetime import date

# ==========================================
# MODELLI PER I CLIENTI (Anagrafica e Fidi)
# ==========================================
class ClienteBase(BaseModel):
    ragione_sociale: str
    partita_iva: Optional[str] = None
    codice_fiscale: Optional[str] = None
    email_amministrazione: Optional[str] = None
    ritardo_medio_giorni: int = 0
    fido_massimo_concesso: float = 0.00
    bloccato_per_morosita: bool = False

class ClienteCreate(ClienteBase):
    pass

class ClienteResponse(ClienteBase):
    id: int
    azienda_id: str

    class Config:
        from_attributes = True


# ==========================================
# MODELLI PER CONTI CORRENTI E CASSA BANCO
# ==========================================
class ContoCorrenteBase(BaseModel):
    nome_banca: str
    filiale: Optional[str] = None
    iban: str
    bic_swift: Optional[str] = None
    tipologia: str = "CONTO_CORRENTE"  # Valori: CONTO_CORRENTE, CASSA_CONTANTI, POS
    valuta: str = "EUR"
    saldo_contabile: float = 0.00

class ContoCorrenteCreate(ContoCorrenteBase):
    pass

class ContoCorrenteResponse(ContoCorrenteBase):
    id: int
    azienda_id: str
    saldo_disponibile: float
    attivo: bool

    class Config:
        from_attributes = True
# ==========================================
# SCHEMI FORNITORI B2B
# ==========================================
class FornitoreCreate(BaseModel):
    ragione_sociale: str
    partita_iva: str
    codice_fiscale: Optional[str] = None
    email_amministrazione: Optional[EmailStr] = None
    iban: Optional[str] = None
    giorni_pagamento_standard: int = 30  # Es. pagamenti a 30, 60, 90 giorni

class FornitoreResponse(FornitoreCreate):
    id: int
    azienda_id: str
# ==========================================
# SCHEMI USCITE RICORRENTI
# ==========================================
class UscitaRicorrenteCreate(BaseModel):
    descrizione: str = Field(..., description="Es. Affitto locale commerciale")
    giorno_scadenza_mese: int = Field(..., ge=1, le=31, description="Giorno del mese (da 1 a 31)")
    importo_stimato: float = Field(..., ge=0.0, description="Importo stimato dell'uscita")
    attivo: Optional[bool] = Field(default=True, description="Se attivo partecipa al cash flow")

class UscitaRicorrenteResponse(UscitaRicorrenteCreate):
    id: int
    azienda_id: str