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
- Umschaltbare Puzzle-Größen per `C`: `4x4`, `5x5`, `6x6`, `8x8`, `10x10`.
- Umschaltbare Themes per `T`: `aurora`, `sunset`, `ocean`, `mono`.
- Umschaltbare Modi per `M`:
  - `Casual` (größere Snap-Toleranz, Rotation aus),
  - `Classic`,
  - `Expert` (kleinere Snap-Toleranz).
- Prozedurale Textur (mit NumPy, fallback ohne NumPy).
- Save/Load über `puzzle_save.json` (`S` / `L`).

## Start

```bash
python3 app.py
```

## Steuerung

- **LMB**: Teil/Gruppe ziehen
- **RMB**: Teil rotieren (90°)
- **H**: Ghost-Image ein/aus
- **S**: Fortschritt speichern
- **L**: Fortschritt laden
- **R**: Neues Puzzle
- **C**: Puzzlegröße wechseln (4x4 / 5x5 / 6x6 / 8x8 / 10x10)
- **T**: Theme wechseln
- **M**: Modus wechseln
