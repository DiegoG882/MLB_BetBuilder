# MLB Bet Builder → Telegram

Herramienta diaria que:
1. Revisa qué picks de días anteriores ganaron o perdieron y **ajusta su calibración** (aprendizaje real, guardado en `data/calibration.json`).
2. Trae los juegos del día, pitchers probables y stats de equipo (MLB Stats API, oficial y gratis).
3. Trae cuotas reales de casas de apuestas (The Odds API, opcional) para comparar contra la probabilidad del modelo.
4. Calcula, por juego: **moneyline** y **total de carreras (over/under)**, cada uno con % de probabilidad y semáforo de riesgo 🟢🟡🔴.
5. Manda todo a tu Telegram.
6. Guarda cada pick en `data/picks_history.json` para revisarlo al día siguiente.

⚠️ **Esto no es una garantía de nada.** Es un modelo estadístico simple con datos públicos. Trátalo como una segunda opinión, no como una verdad — y ponte un límite de cuánto vas a apostar antes de mirar el mensaje del día.

---

## 1. Crear tu bot de Telegram (5 minutos)

1. Abre Telegram y busca **@BotFather**.
2. Mándale `/newbot`.
3. Ponle un nombre (ej. "Mi Bet Builder") y un username que termine en `bot` (ej. `mi_bet_builder_bot`).
4. BotFather te va a dar un **token** parecido a `123456789:AAExampleToken...`. Guárdalo.
5. Ahora necesitas tu **chat_id**:
   - Mándale cualquier mensaje a tu bot recién creado (ej. "hola").
   - Abre en el navegador: `https://api.telegram.org/bot<TU_TOKEN>/getUpdates`
   - Busca en la respuesta JSON el campo `"chat":{"id": 123456789, ...}` — ese número es tu `chat_id`.

## 2. Crear el repo en GitHub

1. Sube esta carpeta a un repo nuevo en GitHub (puede ser privado).
2. En el repo, ve a **Settings → Secrets and variables → Actions → New repository secret** y agrega:
   - `TELEGRAM_BOT_TOKEN` → el token de BotFather
   - `TELEGRAM_CHAT_ID` → tu chat_id
   - `ODDS_API_KEY` → (opcional pero recomendado) tu key de https://the-odds-api.com/ — te registras gratis y te da 500 consultas/mes, más que suficiente para 1 corrida diaria.

## 3. Activar el envío diario

El workflow ya está en `.github/workflows/daily.yml`, configurado para correr **todos los días a las 11:00 AM hora Ciudad de México** (ajustable — mira el comentario en el archivo, es un cron estándar).

- Ve a la pestaña **Actions** de tu repo y confirma que el workflow "MLB Bet Builder Diario" aparece habilitado.
- Para probarlo ya, sin esperar al cron: pestaña Actions → selecciona el workflow → **Run workflow**.

GitHub Actions es gratis para repos privados hasta 2,000 minutos/mes — esto usa unos 2-3 minutos por corrida, así que sobra de sobra.

## 4. Probarlo en tu compu antes (recomendado)

```bash
cd mlb-bet-builder
python3 -m venv venv
source venv/bin/activate        # en Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# edita .env y pon tu TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID y ODDS_API_KEY

python -m src.main
```

Si todo salió bien, te va a llegar el mensaje del día a Telegram y vas a ver el log en la terminal.

**Nota honesta:** este código está escrito contra los endpoints documentados de la MLB Stats API y The Odds API, pero no lo pude probar en vivo desde donde lo generé (sin acceso a internet en ese entorno). Es muy probable que funcione tal cual, pero corrélo local primero (paso 4) antes de confiar en el cron automático, por si algún endpoint necesita un ajuste menor.

## 5. Migrar a tu Raspberry Pi (cuando la tengas)

Es el mismo código. Solo cambia *dónde* corre:

```bash
git clone tu-repo
cd mlb-bet-builder
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # y llena tus valores

crontab -e
# agrega esta línea (corre todos los días a las 11:00 AM hora local):
0 11 * * * cd /ruta/a/mlb-bet-builder && venv/bin/python -m src.main >> log.txt 2>&1
```

Y desactivas el workflow de GitHub Actions para no duplicar mensajes.

---

## Cómo funciona el "aprendizaje" (calibración)

Cada pick que se manda queda guardado como `pending`. Al día siguiente, antes de generar picks nuevos, `settle.py`:

1. Busca el resultado final de cada juego con pick pendiente.
2. Marca el pick como `win` o `loss`.
3. Agrupa por tipo de mercado (`moneyline`, `total_over`, `total_under`) y por rango de probabilidad que predijo el modelo (buckets de 10%: 50-60%, 60-70%, etc).
4. Compara: de todos los picks donde el modelo dijo "70-80% de probabilidad", ¿cuántos realmente ganaron? Si el modelo viene sobre-confiado (dice 75% pero acierta 60% de las veces), `model.py` le resta esa diferencia a futuras predicciones en ese mismo bucket. Si viene desconfiado, se la suma.

Es una calibración simple (no un modelo de machine learning complejo) pero es real: los números de mañana sí cambian según lo que pasó ayer y antier, no es cosmético.

Necesita al menos 5 picks liquidados en un bucket antes de empezar a ajustar ese bucket — al principio (primeras 1-2 semanas) el modelo va a operar con la probabilidad "cruda" porque todavía no tiene suficiente historial.

## Estructura del proyecto

```
mlb-bet-builder/
├── src/
│   ├── mlb_data.py      # datos oficiales: calendario, pitchers, stats, resultados
│   ├── odds_data.py     # cuotas reales de casas de apuestas (The Odds API)
│   ├── model.py         # proyecciones, probabilidad, riesgo, calibración
│   ├── storage.py       # guardar/leer historial y calibración en JSON
│   ├── settle.py        # revisa resultados de días anteriores y actualiza calibración
│   ├── telegram_bot.py  # envío del mensaje
│   └── main.py          # orquestador diario
├── data/
│   ├── picks_history.json   # se va llenando solo, no lo edites a mano
│   └── calibration.json     # se va llenando solo
├── .github/workflows/daily.yml
├── requirements.txt
└── .env.example
```

## Ajustes que probablemente quieras hacer

- **Cuántos juegos incluir por día**: `MAX_GAMES_PER_DAY` en `.env` / secrets.
- **Hora del mensaje**: edita el `cron` en `daily.yml`.
- **Peso temporada vs forma reciente**: en `model.py`, función `project_team_runs` (ahora mismo 60/40).
- **Umbral de los semáforos de riesgo**: en `model.py`, función `risk_label`.
- **Agregar props de bateadores/pitchers** (hits, HR, ponches): la base ya está — `mlb_data.get_pitcher_stats` trae K/9 y últimas 3 aperturas; para bateadores agregarías una función similar usando `/people/{id}/stats` con `group=hitting`. Si quieres, en otra conversación te ayudo a sumar esa parte.
