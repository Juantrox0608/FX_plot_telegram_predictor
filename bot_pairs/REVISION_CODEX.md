# Revisión de Codex: multi-par para JustMarkets

Fecha: 2026-10-08. Rama `v2`. Puesto operaciones, implementación con pruebas; Claude revisa después. No se conectó MT5, no se enviaron órdenes ni mensajes reales, no se leyó `.env`, no se desplegó. Las operaciones descritas por las pruebas son cambios en memoria del simulador.

## Errores por gravedad y correcciones

1. **Crítico: reversión no verificada.** La segunda pata fallaba y el mensaje afirmaba haber cerrado A aunque el cierre fallara. Ahora se reconcilian posiciones por magic y símbolo después de cada envío, se usan tickets de POSICIÓN (no se asume que `result.order` lo sea), se detecta llenado parcial y se intenta cerrar toda la exposición del par. Una reversión fallida deja intención durable de cierre y un evento de error inmediato; nunca declara éxito sin lectura que confirme ausencia de posiciones.
2. **Crítico: patas huérfanas y cierre sin reintento.** Antes podía abrir encima de una B huérfana y solo volvía a intentar al recibir otra vela. Ahora posiciones incompletas/invertidas/duplicadas obligan a cierre, bloquean aperturas y generan aviso; el reintento ocurre antes de pedir velas, incluso sin barra nueva. La intención de cierre se escribe antes de abrir/cerrar y sobrevive al reinicio. Un crash entre patas fuerza reconciliación/cierre, incluso si ambas alcanzaron a abrir.
3. **Crítico: opt-in real solo en aperturas.** Cierres y kill-switch podían ordenar en real sin autorización. Guardas a nivel de ejecución para abrir Y cerrar las posiciones del multi-par: login/servidor exactos, terminal esperado, cuenta USD/hedging, conexión, permisos de trading, `PAIRS_ALLOW_REAL` y `SIGNALS_ONLY`. La cuenta real sin opt-in permite lectura, ninguna orden del multi-par.
4. **Crítico: freno diario volátil y ciclos concurrentes.** Estado por día UTC, equity inicial y bloqueo quedan en SQLite; un reinicio o `/resume` no los borra. Equity cero/negativa también dispara el freno. Las patas se cierran con reintentos y estado verificable, sin afirmar que todas cerraron cuando fallan. Un ciclo por job y exclusión mutua de check/cierre/pausa/reanudación. Bloqueo del SO por carpeta y terminal impide dos procesos sobre la misma instalación; se libera al terminar el proceso.
5. **Alta: lotes/exposición/margen.** Perfil conservador/moderado/agresivo = 0,01/0,02/0,03 por US$1.000 sobre la pata de mayor nocional; otra pata ajustada al nocional USD. Ambas hacia abajo al paso propio, sin subir al mínimo; menos de US$1.000, precios/specs inválidos, pares sin USD o límites incumplidos rechazan apertura. Suma de margen de AMBAS patas debe caber en 90% del margen libre antes de la primera orden; margen desconocido rechaza. No hay margen ficticio ni subida automática del lote.
6. **Alta: cuenta netting y símbolos `.m`.** Netting acumula patas de pares que comparten símbolo: ahora exige hedging USD. Sufijo configurable y sin duplicarlo; lista rechaza pares repetidos/invertidos. Magic base configurable por cliente. Journal/estado por `PAIRS_DATA_DIR`, con scope persistente ligado a cuenta/servidor/terminal/estrategia. `ENV_FILE` y terminal portable por instancia.
7. **Alta: datos inválidos/congelados y correlación NaN.** Terminal desconectado, tick viejo/futuro, historia demasiado antigua, precios inválidos, tiempos desordenados/duplicados o patas con timestamps distintos bloquean señales. Se pide `copy_rates_from_pos(..., 1, ...)`: posición cero en formación excluida explícitamente. Correlación no finita no puede abrir.
8. **Media: fronteras distintas del laboratorio.** Z de log(A)-log(B), std poblacional y correlación de retornos eran iguales. Laboratorio usa `abs(z) >= entry` y stop inclusivo; bot usaba `>`/`<`. Ahora incluye igualdad, salida permanece ±0,3. No se alteran los valores de los parámetros ni se optimiza.
9. **Media: diario y exposición de controles.** El diario de aperturas normales registra lote realmente ejecutado, timeframe configurado y dirección de cada pata; beneficio cerrado incluye swap/comisión/fee. `/cap` y `/dailyrisk` ya no modifican durante la sesión el perfil y el límite revisados: remiten a configuración. Los errores no imprimen respuestas crudas potencialmente privadas y el log de arranque muestra instancia y verificación, sin login/servidor.

## Cambios previos preservados y diferencia de redondeo

Se recibieron sin commit `config.py`, `multi_pair_trader.py` y `.env.example`: salida 0,3, opción size_by_z y plantilla H4 con lookback 120. Se conservaron las opciones de salida/tamaño y la plantilla H4 **no se editó ni incluyó en el commit de Codex**. Los otros dos archivos se integraron al cambio, sin reset/stash/clean. Untracked ajenos en `bot_predictor`, `tmp` y `tools` se dejaron intactos.

Cliente: plantilla NUEVA `.env.cliente.example`, D1, los seis pares del encargo, 60/1,5/4,5/0,7/0,3, size_by_z apagado. Perfil conservador y freno diario 5% son valores iniciales explícitos para demo, pendientes de confirmar con Juan/Claude antes de real; no son garantías de riesgo.

El `risk_profiles.py` del laboratorio redondea hacia abajo el lote base, pero delega el ajustado a `motor_usd.balanced_lots`, que usa redondeo al más cercano. El encargo pide hacia abajo: la versión del bot lo aplica a ambas patas. Ejemplo base 0,03 y ratio 1,59: ajustado 0,04; el cálculo anterior daría 0,05. Se conserva el principio de nocional y perfiles, **no se declara igualdad numérica de los lotes ni se traslada automáticamente el rendimiento del backtest a este ejecutor**. No se modificó el laboratorio ni se hicieron nuevos backtests.

## Verificación reproducible

Desde la raíz del repositorio, con pytest, numpy y pandas instalados:

```powershell
python -m pytest bot_pairs/tests -q
```

Pruebas antes de implementación: 27 fallos y 10 aprobadas; luego cuatro fallos de fronteras; luego seis de validación/terminal y dos de equity no positiva. Cada grupo se ejecutó antes de su corrección. **60 pruebas aprobadas** (1,44 s en la ejecución final). Suite final cubre señales/datos sintéticos sin futuro, redondeo/perfiles, margen, fallos de segunda pata/reversión/llenado parcial, cierres/reintento, reinicio, freno duradero, opt-in real, cuenta/terminal incorrectos, congelamiento, aislamiento y journal. `conftest.py` sustituye MT5 y dotenv ANTES de importar módulos y bloquea sockets. Nunca usa el MT5 instalado ni archivos `.env`.

Skills: python-patterns, tdd-workflow, python-testing, security-review, telegram-bot-builder, backtesting-frameworks, quant-analyst, risk-metrics-calculation; notas de relevo con obsidian-markdown.

## Pendiente antes de activar

- Revisión de Claude y prueba de integración **1–2 días en demo JustMarkets**, configuración D1 exacta del cliente; no realizada en esta entrega.
- Confirmar cuenta USD **hedging**, sufijo/specs reales, lote por perfil elegido, margen, permisos y comportamiento FOK/IOC del servidor. La simulación no certifica esos datos.
- Los stops/salidas son del proceso sobre velas cerradas, sin SL de broker en cada pata. Si VPS/MT5 se caen, no puede ejecutar el cierre hasta recuperar conexión. El freno diario es desde el primer equity observado del día UTC; no reconstruye pérdidas anteriores a la instalación ni promete limitar exactamente el DD durante desconexión/slippage.
- Una operación revertida antes de completar ambas patas puede no quedar como trade completo en el diario. Conciliar movimientos de MT5 con el journal durante demo, incluidos reinicios en medio de la escritura, costos de reversión y cierres manuales. SQLite registra intenciones de seguridad, no sustituye el estado de cuenta del broker.
- Estado/DB existente con distinto scope requiere migración revisada; **no borrarlo ni recrearlo para eludir el freno**. No mover terminal/cuenta/magics con posiciones abiertas.
- No habilitar real ni instalar en VPS en este encargo. Activación posterior solo tras revisión y autorización de Juan.


## Corrección de los cinco hallazgos de Claude (2026-10-08)

1. Reloj: `BrokerClock` verifica el desfase del servidor cada ciclo con cotizaciones en avance; modo automático en múltiplos de 30 minutos o `BROKER_UTC_OFFSET` explícito verificado. Una cotización congelada hace una hora no puede recalibrarse como fresca. Pruebas +2/+3 h, cambio de horario y desconexión. Se respetan las etiquetas de servidor observadas en esta instalación; no se afirma que todas las instalaciones MT5 etiqueten igual.
2. Sesiones: no pide datos ni genera avisos por mercado cerrado el sábado, domingo o lunes antes de 00:02 del servidor. Errores se avisan al aparecer/cambiar, al recuperarse y como máximo una vez por hora si persisten. Horario Forex publicado: lunes 00:02 hasta viernes 23:58:59; las excepciones verificadas se cargan de JSON público por símbolo/fecha. La API Python oficial no expone SymbolInfoSessionTrade: no se declara certificada la sesión específica del terminal ni sus festivos sin esa comprobación en demo.
3. D1: sustituye las 52 horas por la última sesión cerrada esperada y la vela actual en formación. El viernes es válido el lunes; `prime()` no consume esa señal. La fecha procesada persiste para no repetirla al reiniciar.
4. Selecciona cada símbolo antes de pedir cotizaciones; espera de forma acotada la primera cotización válida y bloquea datos congelados.
5. Plantilla anual de 12 meses; aviso una sola vez por vencimiento cuando quedan 15 días o menos. Al vencer bloquea entradas y conserva salidas y reconciliación.

`preflight.py` es diagnóstico sin órdenes, Telegram, journal ni activación de licencia. No carga `.env`: usa el entorno del proceso. Verifica terminal/cuenta demo USD hedging sin imprimir sus identificadores, observa el desfase, lista símbolos/specs, calcula lotes y margen de ambas direcciones y muestra z/correlación y antigüedad de velas. La fachada MT5 permite únicamente consultas y selección de símbolos para obtener datos. **No se ejecutó sobre el MT5 real**: su ejecución futura en demo requiere revisión y autorización.

Pruebas primero: 16 fallos antes de implementar; suite final **78 aprobadas** (4,36 s), incluidos preflight positivo de seis pares y rechazo de cuenta/reloj. MT5 y dotenv simulados; sockets bloqueados. No backtests ni cambios de criterios. `.env.example` y untracked ajenos preservados.

**Diferencia respecto del backtest:** el freno diario del bot cierra las patas abiertas al alcanzar 5%; el backtest citado bloqueaba entradas y dejaba seguir sus salidas normales. No se cambió esa conducta ni se equiparan resultados. Juan debe decidir con el cliente después de revisión. Ambas patas siguen redondeadas hacia abajo, diferencia ya documentada.
