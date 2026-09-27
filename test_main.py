import pytest
from fastapi.testclient import TestClient
from main import app

# Inizializzazione del client di test FastAPI
client = TestClient(app)

def test_health_check():
    """Verifica che l'endpoint di stato del sistema sia online."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "online"
    assert response.json()["rls_active"] is True

def test_login_credenziali_errate():
    """Verifica che il login con credenziali sbagliate venga rifiutato con HTTP 401."""
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "sbagliata@ferramenta.it", "password": "PasswordSbagliata123!"}
    )
    assert response.status_code == 401
    assert "Credenziali non valide" in response.json()["detail"]

def test_flusso_completo_autenticazione_e_saldo():
    """
    Verifica l'intero ciclo di vita dell'autenticazione:
    1. Login corretto -> Ottenimento Token JWT.
    2. Accesso negato a /conti/saldo se manca il token.
    3. Accesso autorizzato a /conti/saldo inviando il Token JWT nell'header.
    """
    # 1. Login
    login_response = client.post(
        "/api/v1/auth/login",
        json={"email": "admin@ferramenta.it", "password": "PasswordSicura123!"}
    )
    assert login_response.status_code == 200
    data = login_response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"

    token = data["access_token"]

    # 2. Tentativo di lettura saldo SENZA Token (deve restituire 401)
    unauth_response = client.get("/api/v1/conti/saldo")
    assert unauth_response.status_code == 401

    # 3. Lettura saldo CON Token JWT (deve restituire 200)
    headers = {"Authorization": f"Bearer {token}"}
    auth_response = client.get("/api/v1/conti/saldo", headers=headers)
    assert auth_response.status_code == 200
    assert "totale_liquidita" in auth_response.json()
    assert "conti" in auth_response.json()