import os
import psycopg2
from dotenv import load_dotenv
from cryptography.fernet import Fernet

load_dotenv(override=True)


def cifra_stringa(testo: str) -> str:
    """Cifra l'IBAN esattamente come fa il database helper"""
    encryption_key = os.getenv("ENCRYPTION_KEY")
    if not encryption_key:
        raise ValueError("Manca la variabile ENCRYPTION_KEY nel file .env!")

    f = Fernet(encryption_key.encode())
    return f.encrypt(testo.encode()).decode()


try:
    conn = psycopg2.connect(
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT")
    )
    cur = conn.cursor()

    # Dati del conto di prova (es. conto corrente della ferramenta)
    azienda_id = "az_001_ferramenta"
    nome_istituto = "Intesa Sanpaolo - Conto Principale"
    iban_chiaro = "IT88X0306909606100000012345"
    iban_cifrato = cifra_stringa(iban_chiaro)
    saldo_iniziale = 15450.50  # 15.450,50 € di liquidità iniziale

    # Inseriamo il conto nel database associato alla tua azienda
    cur.execute("""
        INSERT INTO conti_bancari (azienda_id, nome_istituto, iban, saldo_attuale)
        VALUES (%s, %s, %s, %s);
    """, (azienda_id, nome_istituto, iban_cifrato, saldo_iniziale))

    conn.commit()
    print("✅ Conto bancario di prova inserito con successo!")

    cur.close()
    conn.close()

except Exception as e:
    print(f"Errore durante l'inserimento: {e}")