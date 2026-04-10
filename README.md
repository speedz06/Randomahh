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
- Startmenü vor Spielbeginn für Größe/Theme/Modus:
  - Größen: `4x4`, `5x5`, `6x6`, `8x8`, `10x10`
  - Themes: `aurora`, `sunset`, `ocean`, `mono`
  - Modi: `Casual` (größere Snap-Toleranz, Rotation aus), `Classic`, `Expert`
- Prozedurale Textur (mit NumPy, fallback ohne NumPy).
- 4 Spielstände (Slots) zum Speichern/Laden.

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

- **Hoch/Runter**: Menüpunkt wählen
- **Links/Rechts**: Wert ändern (Größe/Theme/Modus)
- **Enter**: Neues Spiel mit den gewählten Einstellungen starten
- **1..4**: Direkt Spielstand aus Slot laden
