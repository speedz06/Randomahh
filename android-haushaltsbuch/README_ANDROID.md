# Haushaltsbuch Android (Build-Branch)

Dieser Ordner enthält die native Android-Hülle für die Haushaltsbuch-App.

## Datenschutz
In diesem Branch sind **keine persönlichen Haushaltsdaten** gespeichert.
Die APK startet mit leeren Beispieldaten. Eine vorhandene JSON-Datensicherung
wird nach der Installation direkt auf dem Handy importiert.

## Offline
Die App benötigt keine INTERNET-Berechtigung. Der WebView blockiert
Netzwerkzugriffe und lädt ausschließlich die lokale Datei `app/src/main/assets/index.html`.

## Datensicherung
- Import: über den Dateiauswahldialog von Android
- Export: über den Android-Dialog „Datei speichern“
- PDF/Druck: über den Android-Druckdialog

## APK
GitHub Actions baut bei Änderungen am Branch `haushaltsbuch-android` automatisch
eine Debug-APK und lädt sie als Workflow-Artefakt `Haushaltsbuch-APK` hoch.
