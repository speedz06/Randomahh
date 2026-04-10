# High-End Jigsaw Puzzle (Pygame)

Professionelles Jigsaw-Puzzle mit echter Kanten-Geometrie (Nasen/Buchten), Union-Find-Clustering, Ghost-Image-Hilfe, Rotation sowie Save/Load im JSON-Format.

## Features

- Mathematische Puzzleformen pro Teilkante (`top/right/bottom/left`: `+1`, `0`, `-1`).
- Nahtloses Nachbar-Fitting durch invertierte Kantenparameter.
- Exakte Alpha-Maskierung über `pygame.SRCALPHA`.
- Union-Find für permanente Cluster-Verschmelzung.
- Gruppenziehen inklusive Z-Order-Fokus + Schatten.
- Rotation per Rechtsklick (Snapping nur bei `0°`).
- Dirty-Rect-Rendering während Drag für stabile FPS.
- Ghost-Image-Hilfe per `H`.
- Modernes, klickbares Startmenü (UI statt nur Tastatur):
- Startmenü vor Spielbeginn für Größe/Theme/Modus:
  - Größen: `4x4`, `5x5`, `6x6`, `8x8`, `10x10`
  - Themes: `aurora`, `sunset`, `ocean`, `mono`
  - Modi: `Casual` (größere Snap-Toleranz, Rotation aus), `Classic`, `Expert`
  - Auflösungen: `1280x720`, `1366x768`, `1600x900`, `1920x1080`
- Prozedurale Textur (mit NumPy, fallback ohne NumPy).
- 4 Spielstände (Slots) zum Speichern/Laden.
- Wenn ein fortgesetztes Puzzle abgeschlossen wird, wird dessen aktiver Slot automatisch gelöscht.

## Start

```bash
python3 app.py
```

## Steuerung

- **LMB**: Teil/Gruppe ziehen
- **RMB**: Teil rotieren (90°)
- **H**: Ghost-Image ein/aus
- **R**: Neues Puzzle
- **Shift+1..4**: In Slot 1-4 speichern
- **1..4**: Slot 1-4 laden
- **Esc**: Zurück ins Startmenü

### Startmenü

- **Maus**: Klickbare Buttons für Größe, Theme, Modus, Auflösung, Start und Slot-Laden
- **Maus**: Pro Slot zusätzlich ein **Löschen**-Button, um Spielstände manuell zu entfernen
- **Hoch/Runter**: Menüpunkt wählen
- **Links/Rechts**: Wert ändern (Größe/Theme/Modus)
- **Enter**: Neues Spiel mit den gewählten Einstellungen starten
- **1..4**: Direkt Spielstand aus Slot laden
