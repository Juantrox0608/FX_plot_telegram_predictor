# Guía de instalación del multi-par por cliente

Preparación local documentada por Codex el 2026-10-08. **No se ejecutó ninguna instalación ni arranque en el VPS.** Claude revisa primero; Juan autoriza después. Esta guía es para quien administre el VPS.

## Una instalación independiente

Por cliente usar carpetas propias, por ejemplo:

- `C:/Cuanti/cliente_01/bot_pairs/`: copia del código de rama v2 revisada.
- `C:/Cuanti/cliente_01/terminal/`: instalación MT5 JustMarkets del cliente. El ejecutor inicializa en modo portable; la carpeta debe permitir escritura y no compartirse con otra instancia.
- `C:/Cuanti/cliente_01/secrets/cliente.env`: credenciales privadas, fuera de Git y de carpetas compartidas. Permisos Windows solo para administrador y usuario del servicio.
- `C:/Cuanti/cliente_01/runtime/`: journal SQLite, estado diario e intenciones de cierre. Respaldos privados. No subir estas bases ni el `.env`.

Crear otro terminal, entorno Python, bot/chat autorizado, carpeta runtime, `INSTANCE_ID` y rango `PAIRS_MAGIC_BASE` por cliente. Dejar al menos 100 números entre bases. No usar la misma carpeta/terminal para dos procesos: el bloqueo del SO lo rechaza. Mantener los magics al reiniciar; cambian la propiedad de las posiciones.

## Configuración exacta para demo

Copiar `.env.cliente.example` **fuera del repo** y completar allí login, password, servidor exacto, path del terminal y token/chat privados. No pegarlos en chat, logs ni documentación. `.env.example` conserva el experimento H4 anterior: **no usarlo para este cliente**.

La plantilla del cliente define D1 y:

`EURUSD-GBPUSD, EURUSD-AUDUSD, EURUSD-NZDUSD, GBPUSD-AUDUSD, GBPUSD-NZDUSD, USDCHF-USDCAD`.

Sufijo `.m`, lookback 60, entrada 1,5, stop 4,5, correlación mínima 0,7, salida 0,3; `PAIRS_SIZE_BY_Z=false`. Cuenta demo **USD hedging**, no netting: varios pares comparten símbolo. Perfil conservador = 0,01 por US$1.000 en la pata de mayor nocional, otra pata ajustada y ambas redondeadas hacia abajo; saldo menor a US$1.000 no abre. Confirmar perfil y freno diario con Juan antes de pasar a real; la plantilla usa conservador y 5% como configuración inicial de demo. No editar parámetros según resultados de esos días.

`PAIRS_ALLOW_REAL=false` y `MODE=demo`. El guard verifica login/servidor/terminal antes de señales y antes de órdenes. Sin opt-in real también bloquea cierres: no arrancar sin permiso sobre una cuenta real que ya tenga posiciones esperando que las cierre.

## Pruebas y arranque (cuando estén autorizados)

Instalar dependencias del bot en su entorno virtual. Ejecutar la suite desde la raíz de la copia del repositorio:

```powershell
python -m pytest bot_pairs/tests -q
```

Para arrancar después en su carpeta `bot_pairs`:

```powershell
$env:ENV_FILE = 'C:/Cuanti/cliente_01/secrets/cliente.env'
python main_multipair.py
```

El archivo indicado es absoluto. Usar un lanzador/tarea Windows exclusivo, con ese `ENV_FILE` y directorio de trabajo; evitar variables heredadas de otro cliente. Abrir antes el terminal propio y comprobar visualmente cuenta/servidor DEMO y modo hedging USD. Permitir trading algorítmico solo durante la prueba autorizada. Nunca cambiar el login del terminal mientras el proceso está activo.

El inicio imprime etiqueta de instancia y cuenta/servidor verificados. En el chat privado autorizado `/status` debe mostrar DEMO, D1, seis pares, perfil y freno; `/positions` debe coincidir con el terminal. Un error de cuenta, terminal, USD/hedging o datos detiene el ciclo, no se corrige sustituyendo silenciosamente credenciales o precios. Los datos de señal vienen de la vela anterior cerrada; no hay operación garantizada cada día.

## Comprobación durante 1–2 días de demo

Conservar configuración exacta. Registrar cuenta correcta sin divulgar sus datos, specs/lotes/margen, journal, comparación con terminal y reloj; probar reinicio con patas abiertas, cierre manual y fallo/desconexión en una demo controlada. Revisar avisos de pata huérfana, reintentos y freno diario persistente. Si no surge una señal durante esos días, no afirmar que se validó apertura real de las dos patas: extender/verificar ese caso en demo sin alterar umbrales para forzarla.

Un resultado de demo no sustituye el backtest ni garantiza rentabilidad. Mostrar también costos/pérdidas. Revisión de Claude y aprobación explícita de Juan antes de cualquier paso a real o despliegue.

## Pausa, cierre y apagado

- `/stop` pausa nuevas entradas y conserva la gestión de salidas. La pausa persiste al reiniciar.
- `/closeall` pausa y solicita cierre de posiciones de los magics de esta instancia. Si falta una pata o falla el cierre, queda intención durable y aviso; reintenta en cada ciclo. Verificar terminal y `/positions`: no basta el mensaje de solicitud.
- `/resume` solo reanuda con cuenta verificada y sin freno diario activo; al día siguiente requiere reanudación manual si quedó pausado.
- `/cap` y `/dailyrisk` remiten a configuración; no alteran los lotes/límite durante la sesión.
- Tras confirmar que no quedan posiciones ni cierres pendientes, Ctrl+C o detener la tarea/servicio de esa instancia. **Apagar el proceso no cierra posiciones.** No apagarlo dejando una pata huérfana.

No borrar `runtime`, SQLite ni locks manualmente mientras esté activo; el lock se libera al terminar. Nunca recrear la base para quitar el freno. Un scope distinto bloquea el arranque: solicitar migración revisada, con cuenta/terminal correctos y sin posiciones abiertas. Los stops y freno requieren conexión/proceso operativo; no hay SL independiente del broker por pata en esta estrategia.
