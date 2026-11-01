# Rundenzähler

Zähler für Häkel-Sätze: Rundenanzahl einstellen, Start drücken, danach zählt jeder Tipp **irgendwo auf dem Bildschirm** eine Runde hoch.

- Schwarzer Hintergrund, große weiße Zahl
- Vibration pro Runde, lange Vibration wenn der Satz fertig ist; nächster Tipp startet den nächsten Satz
- „Zurück" nimmt eine versehentlich gezählte Runde zurück; Doppeltipps innerhalb von 0,35 s werden ignoriert
- Bildschirm bleibt an (Wake Lock), Stand bleibt nach Neuladen erhalten
- Bluetooth-Auslöser/Tastatur: Leertaste/Enter/Pfeil → zählt, Rücktaste/Pfeil links → zurück

Reine HTML-Datei ohne Build. Über GitHub Pages hosten, auf dem Handy öffnen und „Zum Home-Bildschirm hinzufügen".

## Häkel-Bibliothek (Home-Assistant-Add-on)

Im Ordner [`haekel_bibliothek/`](haekel_bibliothek/) liegt ein Add-on, das Häkelanleitungen (PDF) als Katalog mit Vorschaubildern anzeigt.

Installation: In Home Assistant *Einstellungen → Add-ons → Add-on-Store → ⋮ → Repositories* diese Repo-URL eintragen und danach **Häkel-Bibliothek** installieren und starten. Unter *Info* „In Seitenleiste anzeigen“ aktivieren.
