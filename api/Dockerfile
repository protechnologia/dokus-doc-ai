# Krok 2 — usluga FastAPI (logika biznesowa + API).
# Obraz slim + uzytkownik non-root (zasada projektu: nie dzialamy jako root).
#
# Baza przypieta DIGESTEM, nie samym tagiem (konwencja: piny — jak apache/tika:3.3.0.0-full
# i ollama/ollama:0.31.1). Tag `3.12-slim` jest ruchomy: rebuild na serwerze za miesiac dalby
# inny obraz niz testowany, a wdrozenie ma byc powtarzalne. Tag zostaje przed `@` dla
# czytelnosci, ale wiaze digest. Aktualizacja swiadoma: `docker pull python:3.12-slim`, odczyt
# nowego digestu (`docker image inspect python:3.12-slim --format '{{index .RepoDigests 0}}'`)
# i podmiana ponizej.
FROM python:3.12-slim@sha256:423ed6ab25b1921a477529254bfeeabf5855151dc2c3141699a1bfc852199fbf

# Nie zapisuj .pyc, nie buforuj stdout/stderr (czytelne logi w kontenerze).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Najpierw zaleznosci (lepszy cache warstw), potem kod.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Uzytkownik non-root. UID celowo wysoki (ponad zakres ludzkich userow 1000+),
# co minimalizuje kolizje z realnym uzytkownikiem hosta przy ewentualnym bind-mount.
# 35003 = swiadome echo UID-a obrazu Tiki (35002, narzucony przez apache/tika) —
# wspolne "pasmo" userow kontenerow DOKUS. Sama liczba jest dowolna; wazne: non-root.
RUN useradd --create-home --uid 35003 appuser
USER appuser

EXPOSE 8000

# Uvicorn serwuje aplikacje. Port spojny z healthcheck w docker-compose.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
