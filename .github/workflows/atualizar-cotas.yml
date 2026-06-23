name: Atualizar cotas CVM

on:
  schedule:
    # 12:30 UTC = 9:30 da manhã em Brasília, seg-sáb
    # (a CVM publica os informes às 8h BRT, de segunda a sábado)
    - cron: "30 12 * * 1-6"
  workflow_dispatch: {}   # permite rodar manualmente pelo botão "Run workflow"

permissions:
  contents: write

jobs:
  atualizar:
    runs-on: ubuntu-latest
    steps:
      - name: Baixar o repositório
        uses: actions/checkout@v5

      - name: Configurar Python
        uses: actions/setup-python@v6
        with:
          python-version: "3.12"

      - name: Executar o robô
        run: python atualizar_cotas.py

      - name: Publicar cotas.json (se mudou)
        run: |
          git config user.name "aporta-bot"
          git config user.email "actions@users.noreply.github.com"
          git add cotas.json
          git diff --cached --quiet || git commit -m "Atualiza cotas $(date -u +%Y-%m-%d)"
          git push
