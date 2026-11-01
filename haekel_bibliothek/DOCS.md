# Häkel-Bibliothek

Katalog für Häkelanleitungen als PDF. Erscheint in der Home-Assistant-Seitenleiste als **Häkeln**.

## Anleitungen hinzufügen

- **In der Bibliothek:** auf **+** tippen, die PDF auswählen und optional ein Titelbild und eine Kategorie.
- **Über die Dateien-App (iPhone/iPad):** *Durchsuchen → … → Mit Server verbinden →* `smb://homeassistant.local`.
  Dort unter `share/haekeln` speichern. Neue Dateien erscheinen beim nächsten Öffnen des Katalogs.

## Regeln für den Ordner

- **Unterordner = Kategorie**, z. B. `share/haekeln/Amigurumi/Hase.pdf`
- **Titelbild:** Ein Bild mit demselben Namen neben der PDF (`Hase.jpg` / `Hase.png`) wird als Titelbild verwendet. Gibt es keines, wird Seite 1 der PDF gezeigt.
- **Titel:** Kommt aus dem Dateinamen (`Hase_Hoppel.pdf` → „Hase Hoppel“) und lässt sich in der Bibliothek ändern.
- **Löschen** verschiebt Dateien nach `share/haekeln/.papierkorb`. Dort kann man sie wiederherstellen.

Status, Favoriten, Notizen und geänderte Titel liegen in den Add-on-Daten (`/data/meta.json`). Wird eine Datei per Samba umbenannt oder verschoben, gehen diese Angaben für die Datei verloren.

## Backup

Damit die PDFs gesichert werden, muss bei Backups der Ordner **Share** mit ausgewählt sein.
