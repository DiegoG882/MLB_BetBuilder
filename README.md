# MLB Bet Builder → Telegram

Herramienta diaria que:
1. Revisa qué picks de días anteriores ganaron o perdieron y **ajusta su calibración** (aprendizaje real, guardado en `data/calibration.json`).
2. Trae los juegos del día, pitchers probables y stats de equipo (MLB Stats API, oficial y gratis).
3. Trae cuotas reales de casas de apuestas (The Odds API, opcional) y calcula la probabilidad **sin vig** (justa) del mercado para comparar contra el modelo.
4. Proyecta carreras considerando **ventaja de local**, el abridor probable y el ERA de todo el staff rival (proxy de bullpen).
5. Calcula, por juego: **moneyline** y **total de carreras (over/under)**, cada uno con % de probabilidad, edge vs. mercado, y semáforo de riesgo 🟢🟡🔴.
6. Si le dices tu **bankroll**, sugiere cuánto apostar por pick (Kelly fraccionado) y un resumen de exposición total del día.
7. Manda todo a tu Telegram.
8. Guarda cada pick en `data/picks_history.json` para revisarlo al día siguiente, y expira automáticamente los que se quedan sin resultado por más de 3 días.

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

El workflow ya está en `.github/workflows/daily.yml`, configurado para correr **todos los días a las 11:00 AM hora Ciudad de México** (ajustable — mira el comentario en el archivo, es un cron estándar). Antes de mandar el mensaje corre los tests automáticos (`pytest`) para que un error en el código no te mande un mensaje con números rotos sin que te enteres.

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

# corre los tests para confirmar que todo esta sano
pytest

python -m src.main
```

Si todo salió bien, te va a llegar el mensaje del día a Telegram y vas a ver el log en la terminal.

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

## Bankroll y cuánto apostar por pick (Kelly fraccionado)

Si le dices al sistema cuánto bankroll tienes, cada pick del mensaje diario va a incluir un monto sugerido calculado con el **Criterio de Kelly** (a un cuarto, el estándar de la industria para no apostar de más cuando el edge estimado resulta impreciso), con un tope duro de 5% del bankroll por pick sin importar qué tan grande salga el número.

Para configurar o actualizar tu bankroll:

```bash
python -m src.set_bankroll 500     # lo fija en $500
python -m src.set_bankroll +50     # le suma 50 (ej. depositaste mas)
python -m src.set_bankroll -30     # le resta 30 (ej. retiraste)
python -m src.set_bankroll         # solo muestra el bankroll actual
```

Esto actualiza `data/bankroll.json`. Si corres el bot en GitHub Actions, corre este comando localmente y haz `git push`, o edita el archivo directo desde GitHub (Settings → el archivo → editar → commit). El sistema **nunca** cambia tu bankroll solo — vos decides cuándo actualizarlo según tus resultados reales.

Si no configuras bankroll, el mensaje sigue funcionando igual, solo que sin montos sugeridos (nada más probabilidad, edge y riesgo).

El mensaje diario también incluye un resumen de **exposición total** (cuánto suman todos los picks sugeridos del día vs. tu bankroll), con una advertencia si esa exposición pasa de 15% en un solo día (ajustable con `EXPOSURE_WARNING_PCT` en `.env`).

⚠️ Kelly asume que la probabilidad del modelo es exacta, y nunca lo es del todo. Los montos son una guía matemática, no una instrucción — el límite real de cuánto apostar lo pones vos.

## Cómo funciona el "aprendizaje" (calibración)

Cada pick que se manda queda guardado como `pending`. Al día siguiente, antes de generar picks nuevos, `settle.py`:

1. Busca el resultado final de cada juego con pick pendiente.
2. Marca el pick como `win` o `loss` (o `no_data` si el juego se pospuso/canceló, o si pasaron más de 3 días sin resultado disponible).
3. Agrupa por tipo de mercado (`moneyline`, `total_over`, `total_under`) y por rango de probabilidad que predijo el modelo (buckets de 10%: 50-60%, 60-70%, etc).
4. Compara: de todos los picks donde el modelo dijo "70-80% de probabilidad", ¿cuántos realmente ganaron? Si el modelo viene sobre-confiado (dice 75% pero acierta 60% de las veces), `model.py` le resta esa diferencia a futuras predicciones en ese mismo bucket. Si viene desconfiado, se la suma.

Es una calibración simple (no un modelo de machine learning complejo) pero es real: los números de mañana sí cambian según lo que pasó ayer y antier, no es cosmético.

Necesita al menos 5 picks liquidados en un bucket antes de empezar a ajustar ese bucket — al principio (primeras 1-2 semanas) el modelo va a operar con la probabilidad "cruda" porque todavía no tiene suficiente historial.

## Qué cambió en esta versión

- **Ventaja de local**: los equipos de casa reciben un bono fijo en su proyección de carreras (`model.HOME_FIELD_RUN_BONUS`), reflejando que en MLB el local anota ~4-5% más en promedio.
- **Proxy de bullpen**: la proyección ya no depende solo del ERA del abridor probable — se mezcla con el ERA de todo el staff del equipo rival (abridores + bullpen), porque el abridor rara vez completa el juego.
- **De-vig real**: el edge contra el mercado ahora se calcula contra la probabilidad *justa* (sin la comisión de la casa), no contra la implícita cruda — antes el edge estaba sistemáticamente inflado.
- **Bankroll + Kelly fraccionado**: sugerencia de cuánto apostar por pick y resumen de exposición total del día (ver sección arriba).
- **Reintentos con backoff**: las llamadas a la MLB Stats API, The Odds API y Telegram ahora reintentan automáticamente ante fallos de red transitorios en vez de tumbar la corrida completa.
- **Expiración de picks viejos**: un pick que se queda `pending` más de 3 días (juego pospuesto, endpoint caído) se marca `no_data` en vez de quedar pendiente para siempre.
- **Tests automáticos**: `tests/` cubre las funciones puras de `model.py`, `settle.py` y `odds_data.py` (Poisson, calibración, Kelly, de-vig, resultados). Corren en cada ejecución del workflow antes de mandar el mensaje.

### Ideas que quedaron pendientes (requieren datos que no están disponibles gratis/fácil hoy)

- Splits por mano del pitcher (lefty/righty).
- Factor de clima y parque (Coors Field, etc.).
- Detección de lesiones/bajas de última hora.
- Props de bateadores/pitchers (hits, HR, ponches) — la base ya está en `mlb_data.get_pitcher_stats`; para bateadores se agregaría una función similar usando `/people/{id}/stats` con `group=hitting`.

## Estructura del proyecto

```
mlb-bet-builder/
├── src/
│   ├── mlb_data.py      # datos oficiales: calendario, pitchers, stats, resultados
│   ├── odds_data.py     # cuotas reales + de-vig (The Odds API)
│   ├── model.py         # proyecciones, probabilidad, riesgo, calibración, Kelly
│   ├── storage.py       # guardar/leer historial, calibración y bankroll en JSON
│   ├── settle.py        # revisa resultados de días anteriores, calibra, expira picks viejos
│   ├── set_bankroll.py  # CLI para fijar/ajustar tu bankroll
│   ├── telegram_bot.py  # envío del mensaje (con reintentos)
│   └── main.py          # orquestador diario
├── tests/
│   ├── test_model.py
│   ├── test_settle.py
│   └── test_odds_data.py
├── data/
│   ├── picks_history.json   # se va llenando solo, no lo edites a mano
│   ├── calibration.json     # se va llenando solo
│   └── bankroll.json        # lo actualizas vos con set_bankroll.py
├── .github/workflows/daily.yml
├── requirements.txt
└── .env.example
```

## Ajustes que probablemente quieras hacer

- **Cuántos juegos incluir por día**: `MAX_GAMES_PER_DAY` en `.env` / secrets.
- **Hora del mensaje**: edita el `cron` en `daily.yml`.
- **Peso temporada vs forma reciente**: en `model.py`, función `project_team_runs` (ahora mismo 60/40).
- **Peso abridor vs. staff completo**: en `model.py`, `STARTER_WEIGHT_IN_OPPONENT_FACTOR` (ahora 65/35).
- **Bono de local**: en `model.py`, `HOME_FIELD_RUN_BONUS` (ahora 0.15 carreras).
- **Umbral de los semáforos de riesgo**: en `model.py`, función `risk_label`.
- **Qué tan agresivo es Kelly**: `KELLY_FRACTION` en `.env` (0.25 = un cuarto de Kelly).
- **Tope máximo por pick**: en `model.py`, `MAX_STAKE_PCT_OF_BANKROLL` (ahora 5% del bankroll).
