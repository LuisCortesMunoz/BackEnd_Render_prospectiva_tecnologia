# Banda — Programa maestro v6 (motor configurable)

`ST_banda_v6_motor_configurable.txt` = **el ST v5 vigente, intacto**, más un
**motor configurable**. Un registro decide cuál manda:

| `EngineMode` (%R200) | Qué corre |
|---|---|
| 0 | La lógica v5 de siempre (I1 arranca, eventos S1/S2, contador, torreta v5). |
| 1 | El motor configurable: cualquier señal → condición AND/OR → timer / contador / memoria / secuencia → motor, dirección, torreta y plumas. |

Lo compartido no cambia: arranque seguro (§0), validación (§3), **I3 siempre
detiene todo** (§4), NewCfgFlag/ResetCmd (§6/§7), preparación y frecuencia del
VFD (§8/§9) y feedback/monitores (§18–§20). Se probó que con `EngineMode = 0` el
v6 da **exactamente** las mismas salidas que el v5, scan por scan
(`test_banda_motor.py`, sección 5).

---

## 1. Instalación en Cscape

1. Abre `ladder_maestro_banda.csp` y **guárdalo como copia** (`ladder_maestro_banda_v6.csp`).
   El original queda como respaldo para volver atrás.
2. Crea las variables de **`TAGS_banda_v6.csv`** (109 tags) en la tabla de variables globales:
   - `EC` : arreglo **INT[260]** en **%R200** (configuración; la escribe el backend).
   - `ES` : arreglo **INT[40]** en **%R460** (estado; lo lee el backend).
   - `Sys100ms` : BOOL en **%S4** (reloj T_100MS).
   - El resto son internas (sin dirección, no retentivas): 14 arreglos BOOL, 6 arreglos INT y escalares.
   Si tu Cscape permite importar la tabla de variables desde CSV, úsalo; si no, créalas a mano
   con los nombres, tipos y tamaños de la tabla.
   El rango %R128–%R499 estaba libre en tu `.csp` (verificado); nada del v5 se mueve.
3. Reemplaza el texto del programa ST por el contenido completo de `ST_banda_v6_motor_configurable.txt`.
4. Compila. Si Cscape marca un error de tipo, revisa esa variable en la tabla
   (el nombre y el tipo exactos están en el CSV).
5. Descarga al PLC de la banda.
6. Comprueba en el visor de datos: **%R460 = 6** (el v6 está corriendo) y %R200 = 0.

> El backend se niega a cargar un programa del motor configurable si %R460 ≠ 6,
> y avisa que falta descargar el v6.

## 2. Activar el motor configurable en LAVO

En **Render → Environment** agrega `BANDA_MOTOR_CONFIGURABLE = 1` y redespliega.
Desde ese momento, las instrucciones de la banda se generan como bloques del
motor configurable. Para volver al esquema v5 sin tocar el PLC, pon `0`: el v6
sigue funcionando en modo v5.

## 3. Prueba en la banda (sugerida, en este orden)

1. **Modo v5**: carga un programa v5 de los que ya usabas. Debe comportarse igual que antes.
2. "Cuando presione el botón verde y S2 esté activo enciende la amarilla" (sin movimiento).
3. "A 30 Hz arranca con I1; cuando S1 detecte una caja espera 3 s y cambia el sentido".
   Verifica: la banda **sigue avanzando** los 3 s, **frena** (≥ 2 s y velocidad cero) y luego invierte.
   Una **segunda caja** vuelve a invertir.
4. Presiona **I3** a mitad de cualquier prueba: todo se detiene, y al soltarlo **nada arranca solo**.
5. "Cuando S1 detecte: amarilla, espera 3 s, verde 5 s y apaga" (secuencia de la torreta).

Si algo no queda listo (CfgReady = 0), el panel de la banda muestra el campo exacto
que el PLC rechazó (ES[2]).

## 4. Mapa de registros

**EC (%R200 + offset)** — configuración

| Offset | Campo |
|---|---|
| 0 | EngineMode (0 v5 · 1 motor) |
| 1, 2, 3, 4 | Motor: arranque, paro, modo (0 enclavado · 1 mientras), pausa |
| 5, 6, 7 | Dirección 1, dirección 2, invertir (por flanco) |
| 8 | Paro antes de invertir (0.1 s; mínimo 1 s en el PLC) |
| 9, 10 | Filtro antirrebote de sensores / botones (0.1 s) |
| 11, 12 | Botones virtuales APP1 / APP2 |
| 13 | Tiempo máximo de movimiento de una pluma (0.1 s; 0 = sin límite) |
| 14 | Luces encendidas durante el paro I3 (máscara b0 verde · b1 amarilla · b2 roja) |
| 15 | Secuencia exclusiva (1 = la secuencia manda sola sobre motor y salidas) |
| 20–26 | Fuente de verde, amarilla, roja, P1 sube (Q8), P1 baja (Q9), P2 sube (Q6), P2 baja (Q7) |
| 30 + 6k | Condición Ck+1: Op (1 AND · 2 OR), 4 entradas, flanco (0 nivel · 1 subida · 2 bajada) |
| 78 + 4k | Timer Tk+1: tipo (1 TON · 2 TOF · 3 PULSO · 4 RETARDO), entrada, reset, preset (0.1 s) |
| 110 + 4k | Contador CTk+1: entrada, reset, preset, modo (0 una vez · 1 cíclico) |
| 142 + 3k | Memoria Mk+1: set, reset, prioridad (0 reset · 1 set) |
| 166–170 | Secuencia: inicio, paro, reset, modo (0 una vez · 1 cíclica), número de pasos |
| 172 + 5k | Paso k+1: acciones (bits), avance (0 tiempo · 1 señal · 2 tiempo Y señal · 3 señal O tiempo · 4 flanco), señal, tiempo (0.1 s), siguiente |

Acciones de un paso (bits): 0 verde · 1 amarilla · 2 roja · 3 P1 sube · 4 P1 baja ·
5 P2 sube · 6 P2 baja · 7 marcha · 8 paro · 9 dir 1 · 10 dir 2 · 11 invertir.

**Códigos de señal** (+100 = NOT): 1 I1 · 2 I2 · 3 I3 · 4 S1 · 5 S2 · 6 banda corriendo ·
7 detenida · 8 dir 1 · 9 dir 2 · 10 invirtiendo · 11 CfgReady · 12 VFD en cero · 13 APP1 ·
14 APP2 · 15 siempre · 16–23 C1–C8 · 24–31 T1–T8 · 32–39 CT1–CT8 · 40–47 M1–M8 · 48–63 paso 1–16.

**ES (%R460 + offset)** — estado (solo lectura): 0 versión (6) · 1 válido · 2 primer campo
inválido (offset + 1) · 3 motor (0 detenido · 1 marcha · 2 frenando) · 4 dirección real ·
5 dirección pedida · 6 enclavado · 7 secuencia activa · 8 paso · 9 tiempo del paso ·
10–13 máscaras de condiciones/timers/contadores/memorias · 14 salidas · 15 entradas filtradas ·
16–23 acumulados de timers · 24–31 de contadores · 32 falla de plumas · 33 tiempo de frenado.

## 5. Supuestos que conviene confirmar en la banda

- **Botones**: el prompt asume **I1 = botón verde** e **I2 = botón rojo/auxiliar**; I3 es el paro de emergencia.
  Si en tu botonera el "botón rojo" es I3, avísame y lo ajusto.
- **%R200–%R499 en un arreglo**: si tu versión de Cscape no permite un arreglo INT[260] con dirección,
  la alternativa es declarar escalares; avísame y genero esa variante.
- **Corte de energía**: EC no es retentivo. Tras un corte, vuelve a cargar el programa desde la página
  (igual que con `I1_LampMask` del v5).
- **Velocidad cero**: el frenado espera `VFD_SpeedRaw < 50` (0.5 Hz); si el VFD no reporta velocidad,
  invierte igual a los 5 s extra.
