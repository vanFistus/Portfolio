# Vermögens-Monitor

## Setup
1. Neues GitHub Repository erstellen, z. B. `vermoegens-monitor`
2. Alle Dateien aus dieser ZIP hochladen
3. GitHub: Settings → Pages
4. Source: Deploy from a branch, Branch: main, Folder: /root
5. Actions → Update market data → Run workflow
6. Deine Seite ist danach unter `https://DEINNAME.github.io/vermoegens-monitor/` erreichbar.

Die Kurse werden alle 30 Minuten per GitHub Action geladen und in `data/market_data.json` gespeichert. Dadurch gibt es keine Browser-CORS-Probleme.
