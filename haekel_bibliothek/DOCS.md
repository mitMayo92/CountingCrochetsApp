# Häkel-Bibliothek

Katalog für Häkelanleitungen als PDF. Erscheint in der Home-Assistant-Seitenleiste als **Häkeln**.

## Anleitungen hinzufügen

- **In der Bibliothek:** auf **+** tippen, die PDF auswählen und optional ein Titelbild und eine Kategorie. Große Dateien werden automatisch in Stücken hochgeladen (Home Assistant erlaubt nur 16 MB pro Anfrage).
- **Über die Dateien-App (iPhone/iPad):** *Durchsuchen → … → Mit Server verbinden →* `smb://homeassistant.local`.
  Dort unter `share/haekeln` speichern. Neue Dateien erscheinen beim nächsten Öffnen des Katalogs.

## Regeln für den Ordner

- **Unterordner = Kategorie**, z. B. `share/haekeln/Amigurumi/Hase.pdf`
- **Titelbild:** Ein Bild mit demselben Namen neben der PDF (`Hase.jpg` / `Hase.png`) wird als Titelbild verwendet. Gibt es keines, wird Seite 1 der PDF gezeigt.
- **Titel:** Kommt aus dem Dateinamen (`Hase_Hoppel.pdf` → „Hase Hoppel“) und lässt sich in der Bibliothek ändern.
- **Löschen** verschiebt Dateien nach `share/haekeln/.papierkorb`. Dort kann man sie wiederherstellen.

## Mehrere PDFs pro Anleitung (z. B. Deutsch und Englisch)

In der Detailansicht unter **PDFs**:
- **+ PDF hochladen** – weitere Fassung hochladen; sie wird neben der Haupt-PDF als `Name (Englisch).pdf` gespeichert.
- **+ Aus Bibliothek** – eine bereits vorhandene Anleitung als weitere Fassung übernehmen (sie verschwindet dann als eigener Eintrag; Notizen werden übernommen).
- **Umbenennen** ändert die Bezeichnung (Deutsch, Englisch, …), **Abtrennen** macht die Fassung wieder zu einem eigenen Eintrag.
- **Löschen** verschiebt alle PDFs des Eintrags in den Papierkorb.

Die Verknüpfung steht in den Add-on-Daten, die Dateien selbst werden nicht verschoben.

Status, Favoriten, Notizen und geänderte Titel liegen in den Add-on-Daten (`/data/meta.json`). Wird eine Datei per Samba umbenannt oder verschoben, gehen diese Angaben für die Datei verloren.

## Backup

Damit die PDFs gesichert werden, muss bei Backups der Ordner **Share** mit ausgewählt sein.

## Eigenes Symbol auf dem Home-Bildschirm (iPhone/iPad)

Eine Verknüpfung direkt aus Home Assistant zeigt immer das HA-Symbol. Mit der Kurzbefehle-App geht es mit eigenem Symbol:

1. `app-icon-1024.png` aus diesem Ordner in Fotos sichern.
2. Kurzbefehle → **+** → Aktion **„URL öffnen“** → `homeassistant://navigate/bc9ea047_haekel_bibliothek`
   (öffnet die Bibliothek in der Home-Assistant-App).
3. Oben auf den Namen tippen → **„Zum Home-Bildschirm“** → Namen „Häkeln“ eingeben, beim Symbol **„Foto auswählen“** und das Bild wählen.
