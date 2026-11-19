<img src="clipper/web/static/icon.png" width="72" align="left" alt="">

# ClipStream

Clipping-Dienst für Live-Streams – für jedes Spiel. Überwacht eure Streamer (Twitch, YouTube, Kick), erkennt
Hype-Momente am Chat **und** am Stream selbst, sortiert leere Chat-Ausbrüche aus, schneidet fertige
Hochformat-Shorts mit KI-Untertiteln und optionalem Chat-Overlay und lädt sie per Klick auf YouTube Shorts,
TikTok und Instagram Reels hoch.

```
Stream ──► Aufnahme (Ringpuffer, 15 min) ──► Ton- & Bildanalyse ─┐
Chat   ──► Nachrichten/s, Chatter, Emotes, "clip it"-Rufe ───────┼─► Hype-Score 0-100 ──► Clip
                                                                 │
Clip ──► Whisper-Untertitel ──► Schnitt an Satzgrenzen ──► Facecam oben / Gameplay unten ──► Dashboard ──► ⬆ Upload
```

## Was es kann

- **Mehrere Streamer gleichzeitig** – nur den Handle eintragen (z.B. `papaplatte`), Plattform wählen, fertig.
  Sobald jemand live geht, startet die Aufnahme automatisch. Optional nur bei bestimmten Kategorien (z.B. „gta“).
- **Hype-Erkennung** aus 5 Signalen, jeweils gegen den *eigenen* Normalpegel des Streamers gemessen
  (funktioniert bei 500 und bei 50.000 Zuschauern gleich):
  - Chat-Tempo, Anzahl verschiedener Chatter, Emotes/Slang (KEKW, LUL, 💀, „digga“, „was zur“ …),
    „clip it“-Rufe
  - Lautstärke-Spitzen (Schreien, Lachen) und Bildbewegung (Action, Explosionen) – **auch ohne Chat**
- **Regler Chat ↔ Stream**: wie stark der Chat zählt und wie stark die Reaktion des Streamers (Ton/Bild),
  plus Feinregler für jedes Signal. Wirkt sofort, auch bei laufenden Streams.
- **Filter gegen leere Chat-Ausbrüche**: Chat beantwortet nur eine Frage (ja/nein/Zahlen/Namen), Raids,
  Sub-Geschenkwellen, Bot-Befehle (`!giveaway`) und Spam ohne Reaktion im Stream werden aussortiert. Zusätzlich
  prüft das Transkript, ob der Streamer kurz vorher den Chat etwas gefragt hat. Aussortiertes bleibt unter
  *Clips → Aussortiert* sichtbar und lässt sich per Klick doch rendern.
- **Spezial-Momente** auch ohne großen Hype: 🥚 Easter Eggs & Geheimnisse, 🤖 Glitches & Bugs, 😶 plötzliche
  Stille (Spannung – der Chat hält die Luft an).
- **Chat-Verzögerung** wird automatisch geschätzt, damit der Clip den Moment *vor* der Chat-Reaktion enthält.
- **Hochformat 1080×1920**: Facecam wird automatisch erkannt → oben Facecam, unten Gameplay. Der
  Gameplay-Ausschnitt folgt der Action (geglättet) und hält die Facecam raus. Ohne Facecam: Smart-Crop.
  Facecam kann pro Streamer auch einmal von Hand eingezeichnet werden (zuverlässigste Variante).
- **Layouts pro Clip wählbar**: Facecam oben (klein/mittel/groß), **Facecam + Chat oben**, nur Gameplay,
  **Gameplay + Chat**, ganzes Bild. Der Chat wird pro Clip mitgeschnitten (mit Namensfarben) und kann als
  Overlay ins Video.
- **Untertitel** Wort für Wort mit Hervorhebung (Shorts-Stil), **Hook-Text** in den ersten Sekunden,
  Credit „twitch.tv/name“, Lautstärke normalisiert.
- **Titel, Caption, Hashtags** automatisch (Vorlagen oder optional lokales LLM über Ollama – kostenlos).
- **Dashboard** im Apple-Stil (hell/dunkel automatisch), als Programmfenster oder im Browser, auch vom MacBook
  und Handy: Live-Hype-Kurven, Clip-Liste mit großem Player (↑ ↓ zum Durchgehen), Titel bearbeiten, Layout ändern
  + neu rendern, **1-Klick-Upload** auf mehrere Accounts.
- **„Jetzt clippen“-Button** für Momente, die die Erkennung verpasst.
- **VOD-Modus**: vergangene Streams nachträglich clippen (Twitch-VODs und YouTube-Aufzeichnungen inkl.
  Chat-Replay) – ideal, um die Einstellungen zu testen.
- Läuft auf **Windows (auch AMD-Grafikkarten)** und **Mac (Apple Silicon)**, keine kostenpflichtigen APIs.

---

## Installation

### Windows-PC (z. B. mit Radeon RX 9070 XT)

1. **Python 3.11–3.13** installieren (python.org, Haken bei „Add to PATH“).
2. **ffmpeg** installieren: `winget install Gyan.FFmpeg` (danach Terminal neu öffnen).
3. Im Projektordner:
   ```bat
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   copy config.example.yaml config.yaml
   python -m clipper check
   ```
4. Starten: `start.bat` doppelklicken oder `python -m clipper` → Dashboard auf <http://localhost:8686>.

**Als Programm (eigenes Fenster statt Browser):**
```bat
python -m clipper shortcut
```
legt „ClipStream“ auf dem Desktop und im Startmenü an. Doppelklick startet Überwachung + Dashboard in
einem eigenen Fenster (ohne Konsole). Beim Schließen fragt es nach, denn ohne laufendes Programm entstehen
keine Clips. Das Dashboard bleibt trotzdem für den Mac unter `http://<PC-IP>:8686` erreichbar. Beim ersten
Start fragt die Windows-Firewall einmal nach „pythonw.exe“: für **private Netzwerke** erlauben, sonst kommt
der Mac nicht ran. Läuft schon `start.bat`, öffnet die Verknüpfung nur ein Fenster dazu.
Manuell: `python -m clipper app`. Logs ohne Konsole: `data/logs/clipper.log`.

**Untertitel schneller mit der AMD-Grafikkarte:** Standardmäßig läuft Whisper auf der CPU (funktioniert, ca.
30–60 s pro Clip). Deutlich schneller ist [whisper.cpp](https://github.com/ggml-org/whisper.cpp) mit
**Vulkan** (läuft auf AMD): Vulkan-Build herunterladen/bauen, `ggml-large-v3-turbo.bin` laden und in
`config.yaml` unter `transcription` `whispercpp_bin` und `whispercpp_model` eintragen.
Gerendert wird automatisch mit dem AMD-Hardware-Encoder (`h264_amf`).

### MacBook (Apple Silicon)

```bash
brew install ffmpeg python@3.12
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt mlx-whisper
cp config.example.yaml config.yaml
python -m clipper check
./start.sh
```
Auf dem Mac wird automatisch `mlx-whisper` (sehr schnell) und der VideoToolbox-Encoder genutzt.

### Wer lässt es laufen?

Der Clipper muss laufen, solange gestreamt wird. Empfehlung: **auf dem PC laufen lassen**, der Kollege öffnet
das Dashboard vom MacBook aus über `http://<PC-IP>:8686` (die Adresse steht beim Start im Terminal).
Dafür in `config.yaml` unbedingt `web.password` setzen. Von unterwegs: [Tailscale](https://tailscale.com)
auf PC und Mac/Handy installieren – dann ist das Dashboard überall erreichbar, ohne Ports zu öffnen.

---

## Benutzung

```bash
python -m clipper add papaplatte                 # Twitch (Standard)
python -m clipper add @MontanaBlack              # @ = YouTube
python -m clipper add trymacs --platform kick    # oder: kick:trymacs
python -m clipper                                # Dashboard + Überwachung starten
```
Ganze Links gehen weiterhin. Im Dashboard (Tab **Streamer**) einfach Plattform antippen und Handle eintragen.

| Tab | Was du dort machst |
|---|---|
| **Live** | Status aller Streamer, Hype-Score in Echtzeit mit Kurve, Chat/Min., „Jetzt clippen“ |
| **Clips** | Liste links, großer Player rechts. Titel/Hook/Caption/Hashtags anpassen (speichert automatisch), Accounts antippen, **Hochladen**. Layout + Facecam-Größe ändern → „Neu rendern“. Filter *Aussortiert* zeigt, was der Filter weggelassen hat |
| **Streamer** | Antippen zum Aufklappen: Empfindlichkeit, Sprache, Layout, Facecam-Größe, **Facecam einzeichnen**, Upload-Ziele |
| **Accounts** | YouTube/TikTok/Instagram verbinden |
| **Einstellungen** | Regler Chat ↔ Stream, Empfindlichkeit, Feinregler, Filter + Strenge, Spezial-Momente, Standard-Layout, Chat einblenden, Kategorie-Filter, Hashtags |

**Sprache:** Pro Streamer unter *Sprache*. *Englisch* → englische Untertitel **und** englische Titel, Hooks
und Hashtags. *Automatisch* → Whisper erkennt die Sprache selbst (Titel-Vorlagen dann deutsch).

**Tuning** (Einstellungen): Zu viele Clips von bloßem Chat-Gespamme → Regler Richtung *Stream* und/oder
Filter-*Strenge* hoch. Zu viele schwache Clips → *Empfindlichkeit* Richtung „Weniger“. Zu wenige → Richtung „Mehr“.
Die ersten ~3 Minuten jedes Streams lernt das Tool den normalen Pegel und clippt noch nichts.

**VOD-Modus** (vergangenen Stream clippen):
```bash
python -m clipper vod https://www.twitch.tv/videos/123456789 --streamer streamer1
python -m clipper vod aufnahme.mp4 --streamer streamer1 --chat aufnahme.chat.jsonl
```

**Probelauf ohne echten Stream:** `python -m clipper add file:///C:/pfad/video.mp4` – die Datei wird wie ein
Live-Stream abgespielt (Chat optional als `video.chat.jsonl` daneben, Zeilen `{"t": 12.5, "author": "x", "text": "KEKW"}`).

---

## Upload-Accounts einrichten

> Die Plattformen verlangen für automatische Uploads eine eigene (kostenlose) Entwickler-App. Das ist einmalig
> ca. 30–60 Minuten Arbeit pro Plattform. Bis die Apps geprüft sind, gibt es Einschränkungen (siehe unten).

### YouTube Shorts
1. [Google Cloud Console](https://console.cloud.google.com/) → Projekt anlegen → **YouTube Data API v3** aktivieren.
2. OAuth-Zustimmungsbildschirm (Typ „Extern“) anlegen, eure Google-Accounts als **Testnutzer** eintragen.
3. Anmeldedaten → OAuth-Client-ID → Typ **Desktop-App** → JSON herunterladen → als `client_secret.json`
   in den Projektordner legen.
4. `python -m clipper connect youtube` (pro Kanal einmal).

⚠️ **Wichtig:** Videos aus nicht geprüften API-Projekten werden von YouTube **automatisch auf „privat“**
gesetzt. Für öffentliche Uploads das [API-Audit](https://support.google.com/youtube/contact/yt_api_form)
beantragen. Außerdem: Kontingent 10.000 Einheiten/Tag, ein Upload kostet 1.600 → **ca. 6 Uploads pro Tag**
pro Projekt (Erhöhung kann beantragt werden; notfalls mehrere Projekte).

### TikTok
1. [TikTok for Developers](https://developers.tiktok.com/) → App anlegen → Produkte **Login Kit** und
   **Content Posting API** hinzufügen, Scopes `user.info.basic`, `video.upload`, `video.publish`.
2. Redirect-URI eintragen (muss HTTPS sein, z. B. eure Website oder eine Tailscale-Funnel-URL) und
   `client_key`, `client_secret`, `redirect_uri` in `config.yaml` eintragen.
3. Dashboard → Accounts → TikTok → Login-Link öffnen → nach der Freigabe die URL aus der Adresszeile einfügen.

Modus `inbox` (Standard): Der Clip landet als **Entwurf in der TikTok-App** – dort Sound/Text ergänzen und
posten. Das funktioniert schon vor dem Audit und ist ehrlich gesagt oft besser (Trend-Sounds!).
Modus `direct` postet direkt, ist ohne TikTok-Audit aber nur **privat** möglich.

### Instagram Reels
1. Instagram-Accounts auf **Business** oder **Creator** umstellen.
2. [Meta for Developers](https://developers.facebook.com/) → App (Typ „Business“) → Produkt **Instagram** →
   „API-Einrichtung mit Instagram-Login“ → eure Accounts als Tester hinzufügen → **Access Token generieren**.
3. Token im Dashboard unter Accounts → Instagram einfügen. Wird automatisch verlängert.

Für eure **eigenen** Accounts reicht der Entwicklungsmodus der App – kein App-Review nötig.

---

## Wie die Erkennung funktioniert

Pro Sekunde werden fünf Werte gemessen und mit einem robusten z-Score gegen die letzten 10 Minuten
verglichen („wie ungewöhnlich ist das gerade für *diesen* Stream?“):

| Signal | Gruppe |
|---|---|
| Chat-Nachrichten pro 5 s | Chat |
| Emotes/Slang (lustig, Hype, Schock, Fail, Easter Egg, Glitch) | Chat |
| Verschiedene Chatter | Chat |
| Lautstärke-Spitzen | Stream |
| Bildbewegung | Stream |
| „clip“-Rufe im Chat | Bonus |

Der Regler *Chat ↔ Stream* verteilt das Gewicht zwischen den Gruppen (Standard 65 % / 35 %), die Feinregler
innerhalb der Gruppen. Daraus entsteht der **Hype-Score 0–100**. Ab 72 (einstellbar) startet ein Moment.

Danach prüft der **Filter** jeden Chat-Ausbruch: Wie viel davon sind Emotes/Lachen, wie viel kurze Antworten,
hat der Streamer selbst reagiert, gab es einen Raid? Bot-Befehle und bekannte Bots zählen gar nicht erst.
Zusätzlich gibt es einen **Stream-Moment-Pfad**: Extrem laut + viel Bewegung löst auch ohne Chat aus.
Der Clip beginnt 18 s vor dem Moment (Kontext!) und wird nach dem Transkript an Satzgrenzen geschnitten.
Kategorie (lustig/Hype/Schock/Fail/Action) bestimmt Titel-Vorlage und Hashtags.
Alle Gewichte, Emote-Listen und Schwellen stehen in `clipper/config.py` und können in `config.yaml`
überschrieben werden.

**Optional: lokales LLM** (kostenlos) für bessere Titel und eine zweite Bewertung („KI 8/10“):
[Ollama](https://ollama.com) installieren, `ollama pull qwen2.5:7b-instruct`, in `config.yaml`
`llm.enabled: true`. Läuft auf dem Mac sehr gut, auf der Radeon über ROCm/Vulkan.

---

## Gut zu wissen

- **Schriftliche Erlaubnis** der Streamer einholen (welche Plattformen, welche Accounts, Umsatzbeteiligung).
- **Musik:** Spiel-Radios (z.B. in GTA) und Hintergrundmusik im Stream können auf YouTube/Instagram/TikTok
  Copyright-Claims oder Stummschaltungen auslösen. Clips mit lauter Musik vor dem Upload kurz checken.
- **Passwörter** von Instagram/TikTok/YouTube braucht ClipStream nie – nur die offiziellen Logins (Tab Accounts).
- Speicherbedarf: ca. 1 GB Puffer pro aktivem Streamer + ca. 20–40 MB pro fertigem Clip.
  Verworfene Clips werden gelöscht.
- Absturz oder im Task-Manager beendet: Unter Windows enden die Aufnahmen (streamlink/ffmpeg) automatisch mit,
  liegengebliebene Puffer-Ordner werden beim nächsten Start gelöscht.
- Logs: `data/logs/clipper.log`, Aufnahme-Logs pro Session in `data/buffer/…/capture.log`.

## Fehlersuche

| Problem | Lösung |
|---|---|
| Streamer bleibt „Offline“, obwohl live | `python -m clipper check`; streamlink aktualisieren: `pip install -U streamlink yt-dlp` |
| Kick wird nicht erkannt | Kick blockt manchmal (Cloudflare). `pip install -U curl_cffi streamlink`, Chrome installiert lassen |
| Keine Untertitel | `transcription.backend` prüfen; beim ersten Clip wird das Whisper-Modell (~1,6 GB) geladen. Der Grund steht in `data/logs/clipper.log` („Keine Transkription verfügbar (…)“). Projektordner nicht zu tief verschachteln – Windows scheitert an Pfaden über 260 Zeichen |
| Facecam falsch erkannt | Tab Streamer → „Einzeichnen …“ (einmal pro Streamer), dann bei Clips „Neu rendern“ |
| Rendern langsam | `python -m clipper check` zeigt den Encoder; `libx264` = CPU. FFmpeg-Build mit AMF/VideoToolbox nutzen |

## Tests

```bash
pip install pytest httpx
python -m pytest -q
```
