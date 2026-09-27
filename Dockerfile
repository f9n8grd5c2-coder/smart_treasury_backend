# 1. Immagine base ufficiale Python
FROM python:3.11-slim

# 2. Imposta la directory di lavoro nel container
WORKDIR /app

# 3. Disattiva il buffering dei log Python
ENV PYTHONUNBUFFERED=1

# 4. Copia e installa le dipendenze
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 5. Copia il codice sorgente nel container
COPY . .

# 6. Espone la porta 8000 per l'API FastAPI
EXPOSE 8000

# 7. Comando per avviare l'applicazione su tutte le interfacce di rete (0.0.0.0)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]