# Rodando o liveclips no seu PC

## 1. Pré-requisitos
- **Docker Desktop** instalado e aberto.
- Para cortes longos: desative a suspensão do Windows (Configurações → Sistema → Energia → "Nunca") para o app não parar no meio.

## 2. Configurar
```bash
cp .env.example .env
```
Abra o `.env` e preencha as chaves. O mínimo para rodar o fluxo de aprovação é:
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

### Discord
1. <https://discord.com/developers/applications> → New Application → aba **Bot** → Reset Token → copie para `DISCORD_BOT_TOKEN`.
2. Aba **OAuth2 → URL Generator**: marque `bot` e `applications.commands`; em permissões marque *Send Messages*, *Attach Files*, *Use Slash Commands*. Abra a URL gerada e adicione o bot ao seu servidor.
3. No Discord: Configurações → Avançado → **Modo Desenvolvedor**. Clique com o botão direito no canal → *Copiar ID* (`DISCORD_CHANNEL_ID`) e no seu usuário → *Copiar ID* (`DISCORD_ADMIN_USER_ID`).

### Twitch (opcional)
Só para cortar live em andamento da Twitch: em <https://dev.twitch.tv/console/apps> crie um app e coloque o Client ID e o Client Secret em `TWITCH_CLIENT_ID` e `TWITCH_CLIENT_SECRET`.

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

## 5. Aprovação e calibração
1. Com `DRY_RUN=1` (todas as plataformas começam desligadas), mande alguns links e veja os cards no canal #aprovação (aprovar, ⚡ agora, editar ou descartar).
2. Ligue as plataformas com `/plataforma`; só o que você aprova é publicado. Ajuste a nota mínima com `/min_score`.
3. Calibre a IA com ~30 clipes rotulados (inclua spoiler, conteúdo impróprio, inglês e clipes chatos):
   ```bash
   uv run --env-file .env python scripts/calibrate_ai.py ./calibracao ./calibracao/labels.csv
   ```
   O objetivo é **zero falsos "publicar"**.

## 6. Como usar: colar link → thread → #aprovação → publicar
1. Em discord.com/developers → seu bot → Bot, ligue o **Message Content Intent** e reinicie o app.
2. Cole no canal de links `link [instrução]` (ex.: um podcast do YouTube e o que você quer cortar).
3. Acompanhe o progresso na thread "Cortes #N" que o bot abre na mensagem.
4. Os cortes chegam no #aprovação, com o título do vídeo e o minuto. O limite de duração é `ONDEMAND_MAX_HOURS`.
5. Aprove (ou ⚡ agora) e o clipe é publicado nas plataformas ligadas.

## 7. Publicar de verdade
- Teste 1 publicação real: ligue a plataforma com `/plataforma` no Discord (`DRY_RUN=0`), `docker compose up -d`, aprove 1 corte e apague pelo botão 🗑️ no Discord.
- Emergência: `/pause` no Discord.

### Vídeos com restrição de idade (ou "confirme que não é um robô")

Alguns vídeos do YouTube (muita gameplay de GTA, por exemplo) exigem login. Para esses:

1. Num navegador logado numa conta do YouTube (de preferência uma conta secundária), instale
   uma extensão que exporte cookies no formato Netscape (ex.: "Get cookies.txt LOCALLY").
2. Abra youtube.com, exporte os cookies e salve como `data/youtube-cookies.txt`.
3. Mande o link de novo. Não precisa reiniciar o app.

O arquivo dá acesso à conta: não compartilhe e não suba para o Git (a pasta `data/` já é ignorada).
