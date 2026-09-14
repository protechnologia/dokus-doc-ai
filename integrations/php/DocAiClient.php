<?php

declare(strict_types=1);

/**
 * Klient PHP do API dokus-doc-ai — ekstrakcja tekstu z dokumentu + streszczanie. JEDEN
 * samodzielny plik, bez composera i bez autoloadera: wszystkie klasy (klient + DTO +
 * wyjatki) siedza tu razem, wystarczy `require`.
 *
 * Konfiguracja (adres API, timeouty) jest WSTRZYKIWANA, nie zaszyta na sztywno — klient
 * wpina sie do dowolnej aplikacji bez zmiany logiki, sam adres podajesz z zewnatrz.
 *
 * Transport: czysty cURL (zero zaleznosci runtime) — klient jest "drop-in" w dowolnym
 * projekcie bez ryzyka konfliktu zaleznosci. Wymaga rozszerzen `ext-curl` i `ext-json`.
 *
 * Cztery endpointy API (patrz README projektu, sekcja "API"):
 *   - GET  /health                  -> health()
 *   - POST /extract                 -> extract() / extractFile()
 *   - POST /summarize               -> summarize()
 *   - POST /extract-and-summarize   -> extractAndSummarize() / extractAndSummarizeFile()
 *
 * Przyklad uzycia:
 *     require __DIR__ . '/DocAiClient.php';
 *
 *     use Dokus\DocAi\DocAiClient;
 *     use Dokus\DocAi\Config;
 *     use Dokus\DocAi\ApiException;
 *     use Dokus\DocAi\TransportException;
 *
 *     $client = new DocAiClient(new Config('http://localhost:8000'));
 *     try {
 *         $wynik = $client->extractAndSummarizeFile('/sciezka/pismo.pdf');
 *         echo $wynik->summary;
 *     } catch (ApiException $e) {
 *         // API odpowiedzialo bledem HTTP (4xx/5xx) — np. plik za duzy (413) albo Tika niedostepna (502).
 *         fprintf(STDERR, "Blad API %d: %s\n", $e->statusCode, $e->detail ?? $e->getMessage());
 *     } catch (TransportException $e) {
 *         // Nie udalo sie w ogole dobic do API (siec/timeout).
 *         fprintf(STDERR, "Blad transportu: %s\n", $e->getMessage());
 *     }
 */

namespace Dokus\DocAi;

use CurlHandle;

// =====================================================================================
//  WYJATKI
// =====================================================================================

/**
 * Do czego: wspolna baza wszystkich bledow klienta — pozwala zlapac jednym `catch` cokolwiek,
 * co poszlo nie tak (transport albo odpowiedz API).
 *
 * Flow: konkretne sytuacje rzucaja `TransportException` (nie dobilismy do API) albo
 * `ApiException` (API odpowiedzialo kodem 4xx/5xx) — obie dziedzicza stad.
 */
class DocAiException extends \RuntimeException
{
}

/**
 * Do czego: blad warstwy transportu — w ogole nie dostalismy odpowiedzi HTTP (DNS, brak
 * polaczenia, timeout cURL, przerwana transmisja).
 *
 * Flow: rzucany przez `CurlTransport::send()` na bazie `curl_errno`. Odrozniony od
 * `ApiException` celowo: tu wina lezy po stronie sieci/dostepnosci, a nie tresci odpowiedzi.
 */
class TransportException extends DocAiException
{
}

/**
 * Do czego: API odpowiedzialo kodem bledu HTTP (4xx/5xx). Niesie kod statusu, tresc `detail`
 * z ciala odpowiedzi (FastAPI zwraca `{"detail": "..."}` albo — przy bledach walidacji 422 —
 * `{"detail": [...]}`, ktore skladamy w jeden tekst) oraz `X-Request-ID` do korelacji z logami
 * serwera.
 *
 * Flow: budowany przez `DocAiClient` z odpowiedzi non-2xx (`ApiException::fromResponse`).
 * Helpery `isClientError()`/`isServerError()` rozrozniaja, czy zawinilo wejscie (4xx), czy
 * serwer/brama w gore (5xx) — mapowanie kodow opisuje README projektu przy kazdym endpoincie.
 */
class ApiException extends DocAiException
{
    /**
     * @param int         $statusCode Kod HTTP odpowiedzi (np. 413, 422, 502, 503, 504).
     * @param string|null $detail     Tresc pola `detail` z ciala (komunikat FastAPI), o ile bylo.
     * @param string|null $requestId  Wartosc naglowka `X-Request-ID` (korelacja z logami), o ile byla.
     * @param string      $body       Surowe cialo odpowiedzi (diagnostyka, gdy `detail` nieobecny).
     */
    public function __construct(
        public readonly int     $statusCode,
        public readonly ?string $detail = null,
        public readonly ?string $requestId = null,
        public readonly string  $body = '',
    ) {
        // Komunikat wyjatku: czytelny od reki, ze szczegolami w `detail`/kodzie.
        $msg = sprintf('API zwrocilo HTTP %d', $statusCode);
        if ($detail !== null && $detail !== '') {
            $msg .= ': ' . $detail;
        }
        parent::__construct($msg);
    }

    /**
     * Opis metody:
     * Zbuduj wyjatek z odpowiedzi HTTP — wyluskaj `detail` z JSON-a FastAPI (gdy jest) oraz
     * `X-Request-ID`. FastAPI zwraca `detail` w DWOCH ksztaltach: tekst (bledy zglaszane przez
     * serwis, np. 413/502) albo tablica bledow walidacji zadania (422 z pydantic, np. brak pola,
     * zly typ, niepoprawne proporcje) — tablice skladamy w jeden tekst (`formatValidationErrors`),
     * zeby przyczyna byla w `detail`/komunikacie, a nie tylko w surowym ciele. Tolerancyjnie: gdy
     * cialo nie jest JSON-em, `detail` zostaje `null`, a surowe cialo laduje do `$body`.
     *
     * Przyklad argumentow:
     *     status=413, body='{"detail":"Plik za duzy: ..."}', requestId='abc-123'
     *     status=422, body='{"detail":[{"loc":["body","tail_percent"],"msg":"Input should be a valid integer"}]}'
     *
     * Przyklad wyniku:
     *     ApiException(statusCode=413, detail='Plik za duzy: ...', requestId='abc-123')
     *     ApiException(statusCode=422, detail='body.tail_percent: Input should be a valid integer')
     */
    public static function fromResponse(Response $response): self
    {
        // FastAPI dla bledow zwraca {"detail": ...}; sprobuj wyluskac, ale nie wymuszaj.
        $detail = null;
        // SWIADOMIE bez JSON_THROW_ON_ERROR: na nie-JSON body `json_decode` zwroci null zamiast
        // rzucic — budowa wyjatku opisujacego PIERWOTNY blad HTTP nie moze sie wywrocic na parsowaniu
        // (drugi wyjatek zamaskowalby prawdziwy kod 4xx/5xx). Surowe cialo i tak zachowujemy w `$body`.
        $decoded = json_decode($response->body, true);
        $raw     = is_array($decoded) ? ($decoded['detail'] ?? null) : null;

        // Tekst (HTTPException serwisu) -> bierzemy wprost.
        if (is_string($raw)) {
            $detail = $raw;
        // Tablica (bledy walidacji pydantic, 422) -> skladamy "loc: msg; loc: msg".
        } elseif (is_array($raw)) {
            $detail = self::formatValidationErrors($raw);
        }

        return new self(
            statusCode: $response->statusCode,
            detail:     $detail,
            requestId:  $response->requestId,
            body:       $response->body,
        );
    }

    /**
     * Opis metody:
     * Zloz tablice bledow walidacji FastAPI w jeden czytelny tekst: kazdy blad jako
     * "sciezka.pola: komunikat", bledy rozdzielone "; ". Wpisy bez `msg` pomijamy; `loc` bywa
     * lista stringow i indeksow (np. ["body", "items", 0]) — laczymy kropka. Brak jakiegokolwiek
     * uzytecznego wpisu -> `null` (wtedy przyczyna zostaje tylko w surowym `$body`).
     *
     * Przyklad argumentow:
     *     errors=[['loc' => ['body', 'text'], 'msg' => 'Field required'],
     *             ['loc' => ['body'], 'msg' => 'Value error, head_percent + tail_percent nie moze przekraczac 100 (jest 110).']]
     *
     * Przyklad wyniku:
     *     'body.text: Field required; body: Value error, head_percent + tail_percent nie moze przekraczac 100 (jest 110).'
     */
    private static function formatValidationErrors(array $errors): ?string
    {
        $parts = [];
        foreach ($errors as $error) {
            // Wpis musi byc obiektem z tekstowym `msg` — inaczej nie ma czego pokazac.
            if (!is_array($error) || !isset($error['msg']) || !is_string($error['msg'])) {
                continue;
            }
            // Sciezka pola (gdy jest) jako prefiks: "body.tail_percent: ...".
            $loc     = isset($error['loc']) && is_array($error['loc']) ? implode('.', array_map('strval', $error['loc'])) : '';
            $parts[] = $loc !== '' ? $loc . ': ' . $error['msg'] : $error['msg'];
        }
        return $parts !== [] ? implode('; ', $parts) : null;
    }

    /** Czy to blad po stronie wejscia/klienta (4xx) — np. zle base64, plik za duzy, pusty plik. */
    public function isClientError(): bool
    {
        return $this->statusCode >= 400 && $this->statusCode < 500;
    }

    /** Czy to blad po stronie serwera/bramy w gore (5xx) — np. Tika/LLM niedostepne, timeout. */
    public function isServerError(): bool
    {
        return $this->statusCode >= 500 && $this->statusCode < 600;
    }
}

// =====================================================================================
//  KONFIGURACJA
// =====================================================================================

/**
 * Do czego: wstrzykiwana konfiguracja klienta — adres bazowy API i timeouty. Zadnych adresow
 * na sztywno w logice; tworzymy raz i podajemy do `DocAiClient`.
 *
 * Flow: `new Config('http://localhost:8000')` -> `new DocAiClient($config)`. Wartosci mozna
 * wziac ze srodowiska aplikacji (np. zmiennej srodowiskowej/configu), spojnie z zasada "config z ENV".
 */
final class Config
{
    /**
     * @param string $baseUrl               Adres bazowy API, np. 'http://localhost:8000' (bez koncowego '/').
     * @param float  $timeoutSeconds        Calkowity timeout zadania. Domyslnie 180 s — pipeline laczy
     *                                       OCR (do ~120 s) i LLM (do ~60 s) SEKWENCYJNIE, wiec krotki
     *                                       timeout urywalby dlugie skany. Pod /health/extract mozna obnizyc.
     * @param float  $connectTimeoutSeconds Timeout samego nawiazania polaczenia TCP. Domyslnie 10 s.
     */
    public function __construct(
        public readonly string $baseUrl,
        public readonly float  $timeoutSeconds = 180.0,
        public readonly float  $connectTimeoutSeconds = 10.0,
    ) {
    }

    /**
     * Opis metody:
     * Zwroc adres bazowy bez koncowego ukosnika — zeby sklejanie `baseUrl . '/extract'` nie dalo
     * podwojnego '//'.
     *
     * Przyklad argumentow: (instancja z baseUrl='http://localhost:8000/')
     * Przyklad wyniku: 'http://localhost:8000'
     */
    public function normalizedBaseUrl(): string
    {
        return rtrim($this->baseUrl, '/');
    }
}

// =====================================================================================
//  TRANSPORT (cURL)
// =====================================================================================

/**
 * Do czego: surowa odpowiedz HTTP z transportu — kod statusu, cialo i wyluskany `X-Request-ID`.
 * Wartosciowy obiekt (leaf), bez logiki.
 */
final class Response
{
    public function __construct(
        public readonly int     $statusCode,
        public readonly string  $body,
        public readonly ?string $requestId = null,
    ) {
    }

    /** Czy odpowiedz jest sukcesem (2xx). */
    public function isSuccess(): bool
    {
        return $this->statusCode >= 200 && $this->statusCode < 300;
    }
}

/**
 * Do czego: jedyne miejsce gadajace z siecia — izoluje cURL (analogicznie do tego, jak po
 * stronie serwera `TikaClient` izoluje httpx). Zna timeouty/naglowki/mapowanie bledow cURL,
 * NIE zna formatu ciala ani kontraktow endpointow — operuje na surowych stringach (cialo
 * przychodzi juz zakodowane, odpowiedz oddaje surowa). Kodowanie/dekodowanie JSON jest w
 * `DocAiClient` (tam, gdzie zyje kontrakt "to API mowi JSON-em").
 *
 * Flow: `send()` buduje uchwyt cURL, wysyla surowe cialo z podanymi naglowkami, zwraca
 * `Response`. Blad poziomu cURL (brak polaczenia, timeout) -> `TransportException`. Kody HTTP
 * NIE sa tu interpretowane — to robi `DocAiClient`.
 */
final class CurlTransport
{
    public function __construct(
        private readonly Config $config,
    ) {
    }

    /**
     * Opis metody:
     * Wyslij zadanie HTTP z surowym cialem i podanymi naglowkami; zwroc surowa odpowiedz.
     * Transport NIE zna formatu ciala — kodowanie JSON robi `DocAiClient` (tu dostajemy gotowy
     * string). Po drodze przechwytujemy naglowek `X-Request-ID` dla korelacji z logami serwera.
     *
     * Przyklad argumentow:
     *     method='POST', path='/summarize', body='{"text":"Tresc pisma..."}',
     *     headers=['Accept: application/json', 'Content-Type: application/json']
     *
     * Przyklad wyniku:
     *     Response(statusCode=200, body='{"summary":"...","metadata":{...}}', requestId='abc-123')
     *
     * Raises:
     *     TransportException: blad poziomu cURL (DNS/polaczenie/timeout) — nie dostalismy odpowiedzi.
     *
     * @param string[] $headers Naglowki HTTP w formacie cURL ("Nazwa: wartosc").
     */
    public function send(string $method, string $path, ?string $body = null, array $headers = []): Response
    {
        $url = $this->config->normalizedBaseUrl() . $path;

        $handle = curl_init();
        if (!$handle instanceof CurlHandle) {
            throw new TransportException('Nie udalo sie zainicjowac uchwytu cURL.');
        }

        // Przechwyt naglowka X-Request-ID (serwer go nadaje/propaguje) — przyda sie w `Response`/wyjatku.
        $requestId = null;
        $headerCallback = static function ($_ch, string $line) use (&$requestId): int {
            // Naglowki cURL przychodza linia po linii: "Nazwa: wartosc\r\n".
            if (stripos($line, 'X-Request-ID:') === 0) {
                $requestId = trim(substr($line, strlen('X-Request-ID:')));
            }
            return strlen($line);   // cURL wymaga zwrotu liczby przetworzonych bajtow.
        };

        curl_setopt_array($handle, [
            CURLOPT_URL               => $url,
            CURLOPT_CUSTOMREQUEST     => $method,
            CURLOPT_RETURNTRANSFER    => true,
            CURLOPT_HTTPHEADER        => $headers,
            CURLOPT_HEADERFUNCTION    => $headerCallback,
            CURLOPT_CONNECTTIMEOUT_MS => (int) round($this->config->connectTimeoutSeconds * 1000),
            CURLOPT_TIMEOUT_MS        => (int) round($this->config->timeoutSeconds * 1000),
        ]);
        if ($body !== null) {
            curl_setopt($handle, CURLOPT_POSTFIELDS, $body);
        }

        $responseBody = curl_exec($handle);

        // Blad poziomu transportu: cURL zwraca false i ustawia errno (np. 28 = timeout, 7 = brak polaczenia).
        if ($responseBody === false) {
            $errno = curl_errno($handle);
            $error = curl_error($handle);
            curl_close($handle);

            // Timeout dostaje czytelniejszy komunikat — najczestsza przyczyna przy OCR/LLM.
            if ($errno === CURLE_OPERATION_TIMEDOUT) {
                throw new TransportException(
                    sprintf('Przekroczono timeout (%.0f s) przy %s %s.', $this->config->timeoutSeconds, $method, $url)
                );
            }
            throw new TransportException(sprintf('Blad cURL (%d) przy %s %s: %s', $errno, $method, $url, $error));
        }

        $statusCode = (int) curl_getinfo($handle, CURLINFO_RESPONSE_CODE);
        curl_close($handle);

        return new Response($statusCode, (string) $responseBody, $requestId);
    }
}

// =====================================================================================
//  DTO — wartosciowe obiekty odpowiedzi (odbicie modeli API)
// =====================================================================================

/**
 * Do czego: zuzycie tokenow LLM w jednym wywolaniu (diagnostyka kosztu, nie biznes) — odbicie
 * `LLMUsage` z serwera.
 */
final class Usage
{
    public function __construct(
        public readonly int $promptTokens = 0,
        public readonly int $completionTokens = 0,
        public readonly int $totalTokens = 0,
    ) {
    }

    /**
     * Opis metody: Zbuduj z tablicy `usage` odpowiedzi API; brakujace pola -> 0.
     * Przyklad argumentow: ['prompt_tokens' => 1200, 'completion_tokens' => 90, 'total_tokens' => 1290]
     * Przyklad wyniku: Usage(promptTokens=1200, completionTokens=90, totalTokens=1290)
     */
    public static function fromArray(array $data): self
    {
        return new self(
            promptTokens:     (int) ($data['prompt_tokens']     ?? 0),
            completionTokens: (int) ($data['completion_tokens'] ?? 0),
            totalTokens:      (int) ($data['total_tokens']      ?? 0),
        );
    }
}

/**
 * Do czego: odpowiedz `GET /health` — zdrowie samej uslugi plus stan zaleznosci (np. Tiki).
 * `status` to 'ok' albo 'degraded' (degraded = usluga zyje, ale zaleznosc niedostepna; kod HTTP nadal 200).
 */
final class HealthResult
{
    /**
     * @param array<string,string> $dependencies Stan zaleznosci, np. ['tika' => 'ok'].
     */
    public function __construct(
        public readonly string $status,
        public readonly string $service,
        public readonly string $version,
        public readonly array  $dependencies = [],
    ) {
    }

    /** Czy usluga w pelni zdrowa (status 'ok', a nie 'degraded'). */
    public function isOk(): bool
    {
        return $this->status === 'ok';
    }

    public static function fromArray(array $data): self
    {
        return new self(
            status:       (string) ($data['status']  ?? ''),
            service:      (string) ($data['service'] ?? ''),
            version:      (string) ($data['version'] ?? ''),
            dependencies: (array)  ($data['dependencies'] ?? []),
        );
    }
}

/**
 * Do czego: metadane ekstrakcji — odbicie `ExtractMetadata` z API. Pola `ocr*`/`pages*`
 * mowia, czy poszlo OCR i czy z PDF wzieto tylko pierwsze strony (limit zasobow), zeby
 * konsument wiedzial, ze tresc moze pochodzic z czesci dokumentu.
 */
final class ExtractMetadata
{
    public function __construct(
        public readonly ?string $contentType,
        public readonly ?string $language,
        public readonly int     $charCount,
        public readonly int     $wordCount,
        public readonly bool    $ocrUsed = false,
        public readonly ?int    $pagesTotal = null,
        public readonly ?int    $pagesProcessed = null,
        public readonly bool    $ocrTruncated = false,
    ) {
    }

    public static function fromArray(array $data): self
    {
        return new self(
            contentType:    isset($data['content_type']) ? (string) $data['content_type'] : null,
            language:       isset($data['language'])     ? (string) $data['language']     : null,
            charCount:      (int) ($data['char_count'] ?? 0),
            wordCount:      (int) ($data['word_count'] ?? 0),
            ocrUsed:        (bool) ($data['ocr_used'] ?? false),
            pagesTotal:     isset($data['pages_total'])     ? (int) $data['pages_total']     : null,
            pagesProcessed: isset($data['pages_processed']) ? (int) $data['pages_processed'] : null,
            ocrTruncated:   (bool) ($data['ocr_truncated'] ?? false),
        );
    }
}

/**
 * Do czego: odpowiedz `POST /extract` — wyekstrahowany tekst + metadane (MIME, jezyk, dlugosc,
 * diagnostyka OCR/stron).
 */
final class ExtractResult
{
    public function __construct(
        public readonly string          $text,
        public readonly ExtractMetadata $metadata,
    ) {
    }

    public static function fromArray(array $data): self
    {
        return new self(
            text:     (string) ($data['text'] ?? ''),
            metadata: ExtractMetadata::fromArray((array) ($data['metadata'] ?? [])),
        );
    }
}

/**
 * Do czego: jedna czesc wejscia modelu po trunkacji (poczatek / srodek / koniec) — odbicie
 * `SummarizePart` z API. `percent` to zastosowana proporcja budzetu; `start`/`end` to zakres
 * zachowanego fragmentu, `null` gdy proporcja wynosi 0 (czesc wylaczona).
 *
 * Zakres liczony w ZNAKACH Unicode (nie w bajtach), `start` wlacznie, `end` wylacznie, wzgledem
 * tekstu po stronie klienta: `text` wyslany do `summarize()` albo `DocumentSummary::$text`.
 * Fragment wycina sie przez `mb_substr($text, $part->start, $part->end - $part->start, 'UTF-8')`
 * — NIE przez `substr` (bajty; polskie znaki w UTF-8 zajmuja po 2 bajty).
 */
final class SummarizePart
{
    public function __construct(
        public readonly int  $percent,
        public readonly ?int $start = null,
        public readonly ?int $end = null,
    ) {
    }

    /**
     * Opis metody: Zbuduj z tablicy czesci odpowiedzi API; `start`/`end` = null zostaja null.
     * Przyklad argumentow: ['percent' => 45, 'start' => 0, 'end' => 40467]
     * Przyklad wyniku: SummarizePart(percent=45, start=0, end=40467)
     */
    public static function fromArray(array $data): self
    {
        return new self(
            percent: (int) ($data['percent'] ?? 0),
            start:   isset($data['start']) ? (int) $data['start'] : null,
            end:     isset($data['end'])   ? (int) $data['end']   : null,
        );
    }
}

/**
 * Do czego: trzy czesci wejscia modelu po trunkacji — odbicie `SummarizeParts` z API. Wystepuje
 * tylko, gdy tekst przekroczyl limit znakow modelu (`SummarizeMetadata::$truncated` = true).
 */
final class SummarizeParts
{
    public function __construct(
        public readonly SummarizePart $head,
        public readonly SummarizePart $middle,
        public readonly SummarizePart $tail,
    ) {
    }

    /**
     * Opis metody: Zbuduj z obiektu `parts` odpowiedzi API (klucze `head`/`middle`/`tail`).
     * Przyklad argumentow: ['head' => ['percent' => 45, 'start' => 0, 'end' => 40467], 'middle' => [...], 'tail' => [...]]
     * Przyklad wyniku: SummarizeParts(head=SummarizePart(percent=45, ...), middle=..., tail=...)
     */
    public static function fromArray(array $data): self
    {
        return new self(
            head:   SummarizePart::fromArray((array) ($data['head']   ?? [])),
            middle: SummarizePart::fromArray((array) ($data['middle'] ?? [])),
            tail:   SummarizePart::fromArray((array) ($data['tail']   ?? [])),
        );
    }
}

/**
 * Do czego: metadane summaryzacji — odbicie `SummarizeMetadata` z API. `truncated` mowi, czy
 * tekst przekroczyl limit znakow modelu; wtedy model dostal tylko poczatek, fragment ze srodka
 * i koniec dokumentu (ze znacznikami pominiecia), a `parts` opisuje, ktore to fragmenty.
 * `sentChars` to dlugosc tekstu realnie wyslanego do modelu. `parts` = null dokladnie wtedy,
 * gdy `truncated` = false.
 */
final class SummarizeMetadata
{
    public function __construct(
        public readonly string          $model,
        public readonly int             $inputChars,
        public readonly bool            $truncated,
        public readonly Usage           $usage,
        public readonly int             $sentChars = 0,
        public readonly ?SummarizeParts $parts = null,
    ) {
    }

    public static function fromArray(array $data): self
    {
        return new self(
            model:      (string) ($data['model'] ?? ''),
            inputChars: (int) ($data['input_chars'] ?? 0),
            truncated:  (bool) ($data['truncated'] ?? false),
            usage:      Usage::fromArray((array) ($data['usage'] ?? [])),
            sentChars:  (int) ($data['sent_chars'] ?? 0),
            parts:      isset($data['parts']) && is_array($data['parts']) ? SummarizeParts::fromArray($data['parts']) : null,
        );
    }
}

/**
 * Do czego: odpowiedz `POST /summarize` — streszczenie (wypunktowanie kluczowych pól) + metadane.
 */
final class SummarizeResult
{
    public function __construct(
        public readonly string            $summary,
        public readonly SummarizeMetadata $metadata,
    ) {
    }

    public static function fromArray(array $data): self
    {
        return new self(
            summary:  (string) ($data['summary'] ?? ''),
            metadata: SummarizeMetadata::fromArray((array) ($data['metadata'] ?? [])),
        );
    }
}

/**
 * Do czego: odpowiedz `POST /extract-and-summarize` (pelny pipeline) — streszczenie + PELNY
 * wyekstrahowany tekst + metadane OBU etapow (zagniezdzone, by uniknac kolizji `char_count`
 * ekstrakcji vs `input_chars` summaryzacji). `text` to PELNY tekst, PRZED truncacja pod LLM —
 * `summarization->truncated` mowi, czy model widzial tylko fragmenty, a `summarization->parts`
 * wskazuje ich zakresy w `text`.
 */
final class DocumentSummary
{
    public function __construct(
        public readonly string            $summary,
        public readonly string            $text,
        public readonly ExtractMetadata   $extraction,
        public readonly SummarizeMetadata $summarization,
    ) {
    }

    public static function fromArray(array $data): self
    {
        return new self(
            summary:       (string) ($data['summary'] ?? ''),
            text:          (string) ($data['text'] ?? ''),
            extraction:    ExtractMetadata::fromArray((array) ($data['extraction'] ?? [])),
            summarization: SummarizeMetadata::fromArray((array) ($data['summarization'] ?? [])),
        );
    }
}

// =====================================================================================
//  KLIENT
// =====================================================================================

/**
 * Do czego: wysokopoziomowy klient API dokus-doc-ai — jedno API na cztery endpointy serwera.
 * Zna kontrakty (sciezki, ksztalt wejscia/wyjscia, mapowanie kodow na wyjatki); transport
 * (cURL) deleguje do `CurlTransport`. To jedyna klasa, ktora konsument zwykle widzi.
 *
 * Flow: metoda publiczna -> zlozenie ciala JSON -> `CurlTransport::send()` -> sprawdzenie
 * kodu (non-2xx -> `ApiException`) -> dekodowanie JSON -> zmapowanie na DTO (`*::fromArray`).
 *
 * Przyklad uzycia:
 *     $client = new DocAiClient(new Config('http://localhost:8000'));
 *     $health = $client->health();                                  // GET  /health
 *     $ex     = $client->extractFile('/tmp/pismo.pdf');             // POST /extract
 *     $sum    = $client->summarize('Dluga tresc pisma...');         // POST /summarize
 *     $doc    = $client->extractAndSummarizeFile('/tmp/pismo.pdf'); // POST /extract-and-summarize
 *     $doc    = $client->extractAndSummarizeFile('/tmp/pismo.pdf', headPercent: 45, tailPercent: 55); // bez srodka
 */
final class DocAiClient
{
    private readonly CurlTransport $transport;

    /**
     * @param Config             $config    Adres API + timeouty (wstrzykiwane).
     * @param CurlTransport|null $transport Transport — domyslnie cURL nad `$config`. Wstrzykiwalny
     *                                      glownie pod testy (atrapa transportu, bez sieci).
     */
    public function __construct(
        private readonly Config $config,
        ?CurlTransport $transport = null,
    ) {
        $this->transport = $transport ?? new CurlTransport($config);
    }

    /**
     * Opis metody:
     * Sprawdz zdrowie uslugi (`GET /health`). Endpoint zwraca 200 takze przy 'degraded'
     * (usluga zyje, zaleznosc np. Tika niedostepna), wiec uzyj `HealthResult::isOk()`.
     *
     * Przyklad argumentow: (brak)
     * Przyklad wyniku: HealthResult(status='ok', service='dokus-doc-ai', version='0.1.0', dependencies=['tika'=>'ok'])
     *
     * Raises:
     *     TransportException: nie udalo sie dobic do API.
     *     ApiException:       API zwrocilo niespodziewany kod bledu.
     */
    public function health(): HealthResult
    {
        $data = $this->requestJson('GET', '/health', null);
        return HealthResult::fromArray($data);
    }

    /**
     * Opis metody:
     * Wyekstrahuj tekst z pliku JUZ zakodowanego base64 (`POST /extract`). Gdy masz sciezke do
     * pliku, wygodniej uzyc `extractFile()`. `filename`/`content_type` to opcjonalne podpowiedzi
     * typu dla Tiki; brak -> Tika sama wykrywa typ.
     *
     * Przyklad argumentow:
     *     contentBase64='JVBERi0xLjcK...', filename='pismo.pdf', contentType='application/pdf'
     *
     * Przyklad wyniku:
     *     ExtractResult(text='Tresc...', metadata=ExtractMetadata(contentType='application/pdf', ...))
     *
     * Raises:
     *     ApiException(413): plik wiekszy niz MAX_UPLOAD_BYTES.
     *     ApiException(422): zle base64 / pusty plik / Tika odrzucila plik / brak tresci po ekstrakcji.
     *     ApiException(502): tika-server niedostepny.
     *     TransportException: nie udalo sie dobic do API (siec/timeout).
     */
    public function extract(string $contentBase64, ?string $filename = null, ?string $contentType = null): ExtractResult
    {
        $body = $this->buildDocumentBody($contentBase64, $filename, $contentType);
        $data = $this->requestJson('POST', '/extract', $body);
        return ExtractResult::fromArray($data);
    }

    /**
     * Opis metody:
     * Wczytaj plik z dysku, zakoduj base64 i wyekstrahuj tekst (`POST /extract`). Nazwe pliku
     * (basename) wysylamy jako podpowiedz typu, o ile `contentType` nie podano jawnie.
     *
     * Przyklad argumentow: path='/tmp/pismo.pdf', contentType=null
     * Przyklad wyniku: ExtractResult(text='Tresc...', metadata=ExtractMetadata(...))
     *
     * Raises:
     *     DocAiException: pliku nie da sie odczytac (nie istnieje / brak uprawnien).
     *     (oraz jak `extract()`)
     */
    public function extractFile(string $path, ?string $contentType = null): ExtractResult
    {
        [$base64, $filename] = $this->readFileAsBase64($path);
        return $this->extract($base64, $filename, $contentType);
    }

    /**
     * Opis metody:
     * Streszcz gotowy TEKST (`POST /summarize`) — bez ekstrakcji. Wejscie to czysty string;
     * serwer skleja prompt (wypunktowanie kluczowych pól) i pilnuje truncacji pod okno modelu.
     *
     * Proporcje trunkacji (opcjonalne, liczby calkowite 0–100) dzialaja tylko dla tekstu
     * dluzszego niz limit modelu: `headPercent` = poczatek, `tailPercent` = koniec, srodek dostaje
     * reszte (100 - head - tail); 0 wylacza czesc. `null` = nie wysylamy -> domyslne serwera (45/35).
     *
     * Przyklad argumentow: text='Urzad Skarbowy wzywa do zaplaty zaleglosci...', headPercent=45, tailPercent=55
     * Przyklad wyniku: SummarizeResult(summary='...', metadata=SummarizeMetadata(model='gpt-4o-mini', truncated=false, parts=null, ...))
     *
     * Raises:
     *     ApiException(422): puste/same biale znaki wejscie / proporcje spoza 0–100 albo o sumie powyzej 100.
     *     ApiException(500): zla konfiguracja dostawcy LLM po stronie serwera / zly klucz.
     *     ApiException(502): inny blad po stronie dostawcy LLM.
     *     ApiException(503): dostawca LLM dlawi (limit/kwota).
     *     ApiException(504): dostawca LLM nie odpowiedzial w czasie.
     *     TransportException: nie udalo sie dobic do API.
     */
    public function summarize(string $text, ?int $headPercent = null, ?int $tailPercent = null): SummarizeResult
    {
        $body = $this->withTruncationParams(['text' => $text], $headPercent, $tailPercent);
        $data = $this->requestJson('POST', '/summarize', $body);
        return SummarizeResult::fromArray($data);
    }

    /**
     * Opis metody:
     * Pelny pipeline na pliku JUZ w base64 (`POST /extract-and-summarize`): plik -> tekst ->
     * streszczenie w jednym wywolaniu. Dla sciezki na dysku uzyj `extractAndSummarizeFile()`.
     * Proporcje trunkacji jak w `summarize()` — tna TYLKO wejscie modelu; `text` w wyniku jest pelny.
     *
     * Przyklad argumentow:
     *     contentBase64='JVBERi0xLjcK...', filename='pismo.pdf', contentType='application/pdf',
     *     headPercent=null, tailPercent=null
     *
     * Przyklad wyniku:
     *     DocumentSummary(summary='Urzad wzywa...', text='Pelna tresc...',
     *         extraction=ExtractMetadata(...), summarization=SummarizeMetadata(...))
     *
     * Raises:
     *     ApiException(413): plik za duzy. ApiException(422): zle base64 / pusty plik / brak tresci / zle proporcje.
     *     ApiException(500): config LLM. ApiException(502): Tika/LLM. ApiException(503): limit LLM.
     *     ApiException(504): timeout LLM. TransportException: nie udalo sie dobic do API.
     */
    public function extractAndSummarize(
        string $contentBase64,
        ?string $filename = null,
        ?string $contentType = null,
        ?int $headPercent = null,
        ?int $tailPercent = null,
    ): DocumentSummary {
        $body = $this->buildDocumentBody($contentBase64, $filename, $contentType);
        $body = $this->withTruncationParams($body, $headPercent, $tailPercent);
        $data = $this->requestJson('POST', '/extract-and-summarize', $body);
        return DocumentSummary::fromArray($data);
    }

    /**
     * Opis metody:
     * Wczytaj plik z dysku i przepusc przez pelny pipeline (`POST /extract-and-summarize`).
     * Wygodna metoda end-to-end: oryginalny plik -> zwrotnie streszczenie. Proporcje trunkacji
     * jak w `summarize()`; wygodnie przez argumenty nazwane: `headPercent: 45, tailPercent: 55`.
     *
     * Przyklad argumentow: path='/tmp/pismo.pdf', contentType=null, headPercent=45, tailPercent=55
     * Przyklad wyniku: DocumentSummary(summary='...', text='...', extraction=..., summarization=...)
     *
     * Raises:
     *     DocAiException: pliku nie da sie odczytac.
     *     (oraz jak `extractAndSummarize()`)
     */
    public function extractAndSummarizeFile(
        string $path,
        ?string $contentType = null,
        ?int $headPercent = null,
        ?int $tailPercent = null,
    ): DocumentSummary {
        [$base64, $filename] = $this->readFileAsBase64($path);
        return $this->extractAndSummarize($base64, $filename, $contentType, $headPercent, $tailPercent);
    }

    // --- Wewnetrzne helpery -----------------------------------------------------------

    /**
     * Opis metody:
     * Dolacz do ciala zadania proporcje trunkacji (`head_percent`/`tail_percent`) — tylko te
     * podane; `null` pomijamy, bo brak klucza = domyslna serwera, a jawny `null` serwer odrzuca
     * (422). Walidacji zakresu tu NIE dublujemy — robi ja serwer i zwraca czytelny `detail`.
     *
     * Przyklad argumentow: body=['text' => 'Tresc...'], headPercent=45, tailPercent=null
     * Przyklad wyniku: ['text' => 'Tresc...', 'head_percent' => 45]
     */
    private function withTruncationParams(array $body, ?int $headPercent, ?int $tailPercent): array
    {
        if ($headPercent !== null) {
            $body['head_percent'] = $headPercent;
        }
        if ($tailPercent !== null) {
            $body['tail_percent'] = $tailPercent;
        }
        return $body;
    }

    /**
     * Opis metody:
     * Zloz cialo zadania dla endpointow plikowych (/extract, /extract-and-summarize): zawsze
     * `content_base64`, a `filename`/`content_type` tylko gdy podane (null -> autodetekcja Tiki,
     * wiec nie wysylamy pustych pol).
     *
     * Przyklad argumentow: base64='JVBE...', filename='pismo.pdf', contentType=null
     * Przyklad wyniku: ['content_base64' => 'JVBE...', 'filename' => 'pismo.pdf']
     */
    private function buildDocumentBody(string $contentBase64, ?string $filename, ?string $contentType): array
    {
        $body = ['content_base64' => $contentBase64];
        if ($filename !== null) {
            $body['filename'] = $filename;
        }
        if ($contentType !== null) {
            $body['content_type'] = $contentType;
        }
        return $body;
    }

    /**
     * Opis metody:
     * Wczytaj plik z dysku i zwroc [base64, basename]. Basename sluzy jako podpowiedz typu dla
     * Tiki (rozszerzenie). Nieczytelny plik -> `DocAiException` (blad uzycia po stronie klienta,
     * jeszcze przed siecia).
     *
     * Przyklad argumentow: path='/tmp/pismo.pdf'
     * Przyklad wyniku: ['JVBERi0xLjcK...', 'pismo.pdf']
     *
     * Raises:
     *     DocAiException: plik nie istnieje albo nie da sie go odczytac.
     */
    private function readFileAsBase64(string $path): array
    {
        // Czytamy bez tlumienia bledow, ale z jasnym komunikatem — to blad lokalny, nie sieciowy.
        if (!is_file($path) || !is_readable($path)) {
            throw new DocAiException(sprintf('Nie mozna odczytac pliku: %s', $path));
        }
        $contents = file_get_contents($path);
        if ($contents === false) {
            throw new DocAiException(sprintf('Blad odczytu pliku: %s', $path));
        }
        return [base64_encode($contents), basename($path)];
    }

    /**
     * Opis metody:
     * Tu zyje caly kontrakt "to API mowi JSON-em" — w obie strony. Zakoduj cialo do JSON,
     * zloz naglowki, wyslij przez transport (surowe stringi) i zdekoduj odpowiedz na tablice.
     * Mapowanie kodow na wyjatki: non-2xx -> `ApiException` (z `detail` i `X-Request-ID`);
     * 2xx z niepoprawnym JSON -> `DocAiException` (serwer zlamal kontrakt).
     *
     * Przyklad argumentow: method='POST', path='/summarize', body=['text' => 'Tresc...']
     * Przyklad wyniku: ['summary' => '...', 'metadata' => [...]]
     *
     * Raises:
     *     DocAiException:     nie udalo sie zakodowac ciala zadania do JSON; lub 2xx z niepoprawnym JSON.
     *     ApiException:       odpowiedz HTTP 4xx/5xx.
     *     TransportException: blad transportu (propagowany z `CurlTransport`).
     */
    private function requestJson(string $method, string $path, ?array $body): array
    {
        // 1. Kodowanie zadania: cialo (gdy jest) -> JSON; `JSON_UNESCAPED_UNICODE` = polskie znaki
        //    w czystej formie. Brak ciala (GET) -> nie wysylamy `Content-Type`.
        $headers = ['Accept: application/json'];
        $payload = null;
        if ($body !== null) {
            try {
                $payload = json_encode($body, JSON_THROW_ON_ERROR | JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
            } catch (\JsonException $e) {
                throw new DocAiException('Nie udalo sie zakodowac ciala zadania do JSON: ' . $e->getMessage(), 0, $e);
            }
            $headers[] = 'Content-Type: application/json';
        }

        // 2. Transport: surowe cialo + naglowki (cURL nie zna formatu).
        $response = $this->transport->send($method, $path, $payload, $headers);

        // 3. Kazdy kod spoza 2xx to blad API — niezaleznie od tresci ciala.
        if (!$response->isSuccess()) {
            throw ApiException::fromResponse($response);
        }

        // 4. Dekodowanie odpowiedzi: 2xx z zepsutym JSON -> serwer zlamal kontrakt.
        try {
            $decoded = json_decode($response->body, true, 512, JSON_THROW_ON_ERROR);
        } catch (\JsonException $e) {
            throw new DocAiException(sprintf('Niepoprawny JSON w odpowiedzi (HTTP %d): %s', $response->statusCode, $e->getMessage()), 0, $e);
        }

        // Kontrakt: kazdy nasz endpoint zwraca obiekt JSON (mapowany na tablice asocjacyjna).
        if (!is_array($decoded)) {
            throw new DocAiException(sprintf('Oczekiwano obiektu JSON w odpowiedzi, dostano: %s', gettype($decoded)));
        }

        return $decoded;
    }
}
