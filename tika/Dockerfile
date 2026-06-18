# Krok 1 — warstwa ekstrakcji/OCR.
# Rozszerzamy oficjalny obraz Tika-full o polski pakiet jezykowy Tesseract.
# Tag przypiety celowo (powtarzalnosc buildow) — najnowszy full to 3.3.0.0.
FROM apache/tika:3.3.0.0-full

# Obraz -full ma w srodku Tesseract, PDFBox i POI, ale BRAK polskiego
# pakietu jezykowego (tesseract-ocr-pol). Bez niego OCR skanow leci po
# angielsku i polskie znaki wychodza jako krzaki.
# curl dokladamy wylacznie pod healthcheck w docker-compose.
# apt-get wymaga roota; obraz bazowy dziala jako UID:GID 35002:35002.
USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        tesseract-ocr-pol \
        curl \
 && rm -rf /var/lib/apt/lists/*

# Wlasna konfiguracja OCR: domyslny jezyk pol+eng oraz OCR dla skanow w PDF.
COPY tika-config.xml /tika-config.xml

# Powrot do uzytkownika non-root z obrazu bazowego (zasada: nie dzialamy jako root).
USER 35002:35002

# Entrypoint obrazu bazowego uruchamia TikaServerCli i doklejá argumenty CMD.
# W ten sposob wskazujemy nasza konfiguracje OCR.
CMD ["-c", "/tika-config.xml"]
