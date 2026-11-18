# Rodando o liveclips no seu PC

## 1. Pré-requisitos
- **Docker Desktop** instalado e aberto.
- Para a noite do lançamento: desative a suspensão do Windows (Configurações → Sistema → Energia → "Nunca") e, se puder, use cabo de rede.

## 2. Configurar
```bash
cp .env.example .env
```
Abra o `.env` e preencha as chaves. O mínimo para rodar o fluxo de aprovação é:
- `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET`, `TWITCH_BOT_REFRESH_TOKEN`
- `GEMINI_API_KEY` (e/ou `ANTHROPIC_API_KEY`) e `GROQ_API_KEY`
- `DISCORD_BOT_TOKEN`, `DISCORD_CHANNEL_ID`, `DISCORD_ADMIN_USER_ID`

Nota: as plataformas ligadas são controladas pelo `/plataforma` no Discord; `DRY_RUN=1` só faz todas começarem desligadas.

## 3. Subir
```bash
docker compose up -d --build
```
Acompanhar os logs:
```bash
docker compose logs -f app
```
Em ~1 minuto chega a mensagem "liveclips iniciado" no canal do Discord. Teste `/status` no canal.

Parar: `docker compose down`. Banco e vídeos ficam em `./data` e sobrevivem a reinícios.

## 4. Como obter cada credencial

### Twitch
1. Em <https://dev.twitch.tv/console/apps> crie um app. Em "OAuth Redirect URLs" coloque `http://localhost:3000`.
2. Copie o Client ID e gere um Client Secret.
3. No navegador, entre na **conta bot** da Twitch (não na sua pessoal) e rode:
   ```bash
   uv run python scripts/twitch_oauth.py SEU_CLIENT_ID SEU_CLIENT_SECRET
   ```
   Cole o `TWITCH_BOT_REFRESH_TOKEN` que aparecer no `.env`.

### Discord
1. <https://discord.com/developers/applications> → New Application → aba **Bot** → Reset Token → copie para `DISCORD_BOT_TOKEN`.
2. Aba **OAuth2 → URL Generator**: marque `bot` e `applications.commands`; em permissões marque *Send Messages*, *Attach Files*, *Use Slash Commands*. Abra a URL gerada e adicione o bot ao seu servidor.
3. No Discord: Configurações → Avançado → **Modo Desenvolvedor**. Clique com o botão direito no canal → *Copiar ID* (`DISCORD_CHANNEL_ID`) e no seu usuário → *Copiar ID* (`DISCORD_ADMIN_USER_ID`).

### IA
- Gemini: <https://aistudio.google.com/apikey>
- Groq: <https://console.groq.com/keys>
- Anthropic (reserva paga): <https://platform.claude.com> → API Keys, e adicione créditos.

### Instagram
1. A conta precisa ser **Business ou Creator** e estar ligada a uma **Página do Facebook**.
2. Em <https://developers.facebook.com> crie um app (tipo *Business*), adicione o produto **Instagram** e garanta as permissões `instagram_basic`, `instagram_content_publish`, `pages_show_list`, `pages_read_engagement`. Sua conta precisa ter papel no app (modo desenvolvimento não exige revisão).
3. No **Graph API Explorer**, gere um token de usuário com essas permissões e troque por um **token longo (60 dias)**:
   ```bash
   curl "https://graph.facebook.com/v23.0/oauth/access_token?grant_type=fb_exchange_token&client_id=APP_ID&client_secret=APP_SECRET&fb_exchange_token=TOKEN_CURTO"
   ```
4. Descubra o `IG_USER_ID`:
   ```bash
   curl "https://graph.facebook.com/v23.0/me/accounts?fields=instagram_business_account&access_token=TOKEN_LONGO"
   ```
   Use o `instagram_business_account.id`. Coloque o token longo em `IG_ACCESS_TOKEN`.
   > O token vence em 60 dias: anote para renovar.

### TikTok / YouTube
Ficam em `off` até as auditorias saírem. Depois, configure as chaves e mude o modo para `public`.
O TikTok também aceita `TIKTOK_MODE=draft`: o vídeo vai como rascunho no app do TikTok (precisa do scope `video.upload`).

## 5. Aprovação (GTA V) e calibração
1. Com `DRY_RUN=1` (todas as plataformas começam desligadas) e `TWITCH_GAME_NAME=Grand Theft Auto V`, deixe rodando 1–2 h em horário de pico.
2. Veja os cards no canal #aprovação (aprovar, ⚡ agora, editar ou descartar) e ligue as plataformas com `/plataforma`; só o que você aprova é publicado. Ajuste `SPIKE_*`, `RANK_THRESHOLD` e a nota (`/min_score`).
3. Calibre a IA com ~30 clipes rotulados (inclua spoiler, conteúdo impróprio, inglês e clipes chatos):
   ```bash
   uv run --env-file .env python scripts/calibrate_ai.py ./calibracao ./calibracao/labels.csv
   ```
   O objetivo é **zero falsos "publicar"**.

## 6. Lançamento
- Teste 1 publicação real: ligue a plataforma com `/plataforma` no Discord (`DRY_RUN=0`), `docker compose up -d`, espere 1 post e apague pelo botão 🗑️ no Discord.
- Quarta ~23:30: `TWITCH_GAME_NAME=Grand Theft Auto VI`, plataformas ligadas via `/plataforma`, `docker compose up -d`, e confira `/status`.
- Emergência: `/pause` no Discord.
