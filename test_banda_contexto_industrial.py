# -*- coding: utf-8 -*-
"""
============================================================================
PRUEBAS DEL CONTEXTO INDUSTRIAL DE LA BANDA  (capa A + capa B)
============================================================================

Dos bloques:

  REGRESION (§19)  Las instrucciones especificas que ya funcionaban deben
                   producir EXACTAMENTE la misma configuracion que antes de
                   agregar el contexto industrial.

  CONTEXTO NUEVO (§20)  Instrucciones conceptuales ("banda tipo PepsiCo",
                   "quiero clasificacion"): arquetipo identificado, hardware
                   real, nada inventado y parametros faltantes marcados.

Las pruebas son DETERMINISTAS: no llaman al modelo. Cada caso lleva la
intencion que el LLM debe devolver y se comprueba todo lo que viene despues
(normalizador, cobertura, validacion contra el ST) mas el router.

    python test_banda_contexto_industrial.py

Con GROQ_API_KEY en el entorno se puede ademas probar el LLM de verdad:

    python test_banda_contexto_industrial.py --live
============================================================================
"""

import json
import sys

import banda_contexto
import banda_intent
import device_router

FALLOS = []
PRUEBAS = [0]


def check(cond, titulo, detalle=""):
    PRUEBAS[0] += 1
    if cond:
        print(f"  OK   {titulo}")
    else:
        print(f"  FALLA {titulo}" + (f"\n        {detalle}" if detalle else ""))
        FALLOS.append(titulo)


def _intent(**kw):
    """Intencion completa con los valores por defecto del esquema de siempre."""
    base = {
        "movimiento": {"mover": False, "direccion": None, "frecuencia_hz": None,
                       "boton_inicio": None, "paro_automatico": None},
        "paros": {"i2": False, "software": False},
        "eventos": [],
        "luces": {"corriendo": [], "detenida": [], "mientras_i1": []},
        "luz_temporizada": None,
        "plumas_manual": {"pluma1": None, "pluma2": None},
        "no_soportado": [],
    }
    base.update(kw)
    return base


def _evento(sensor, **kw):
    ev = {"sensor": sensor, "conteo": None, "banda": "no_afecta", "duracion_s": None,
          "luces": [], "pluma1": None, "pluma2": None, "al_contar": None}
    ev.update(kw)
    return ev


def _al_contar(**kw):
    ac = {"pausar_banda": False, "detener_proceso": False, "luces": [], "direccion": None,
          "pluma1": None, "pluma2": None, "duracion_s": None}
    ac.update(kw)
    return ac


def pipeline(intent):
    """Todo lo que ocurre DESPUES del LLM: forma -> normalizador -> cobertura
    -> validacion contra el ST. Devuelve (band, errores)."""
    errores = banda_intent.validar_intencion(intent)
    if errores:
        return None, errores
    band, errores = banda_intent.normalizar_intencion(intent)
    cfg = {"name": "test", "device": "banda", "intent": intent, "band": band, "outputs": []}
    errores += banda_intent.errores_cobertura(cfg)
    try:
        import plc_banda
        errores += plc_banda.validar_config(cfg)
    except ImportError:                                     # pragma: no cover
        print("  (aviso) plc_banda no importable: se omite la validacion contra el ST")
    return band, errores


def caso(titulo, texto, intent, esperado, equipo="banda", pendiente=None):
    """Un caso completo: router + pipeline + campos esperados del ST.

    `pendiente` = slot que el sistema debe PREGUNTAR antes de ejecutar (p. ej.
    "frecuencia"): en ese caso el ST rechaza la configuracion a proposito y lo
    que se comprueba es que se pregunte, no que pase."""
    print(f"\n{titulo}\n  texto: {texto!r}")
    det = device_router.detectar_dispositivo(texto)
    check(det["device"] == equipo, f"router -> {equipo}", f"dio {det}")
    band, errores = pipeline(intent)
    if pendiente:
        p = banda_intent.pregunta_pendiente(intent)
        check(p is not None and p["slot"] == pendiente,
              f"pregunta por '{pendiente}' en vez de inventar el dato", str(p))
        errores = [e for e in errores if "frecuencia" not in e]
    check(not errores, "sin errores de normalizacion/cobertura/ST", "; ".join(errores))
    if band is None:
        return None
    for campo, valor in esperado.items():
        check(band.get(campo) == valor, f"band.{campo} = {valor!r}",
              f"dio {band.get(campo)!r}")
    return band


# ===========================================================================
# BLOQUE 1 — REGRESION (§19): lo que ya funcionaba sigue igual
# ===========================================================================
def regresion():
    print("\n" + "=" * 74)
    print("REGRESION (§19) — instrucciones especificas de siempre")
    print("=" * 74)

    caso("1. 'avanza a la derecha'", "avanza a la derecha",
         _intent(movimiento={"mover": True, "direccion": "derecha", "frecuencia_hz": None,
                             "boton_inicio": None, "paro_automatico": None}),
         {"enable": True, "direction": "derecha", "freq_hz": None, "start_button": "I1"},
         pendiente="frecuencia")

    caso("2. 'avanza a 20 Hz'", "avanza a 20 Hz",
         _intent(movimiento={"mover": True, "direccion": "derecha", "frecuencia_hz": 20,
                             "boton_inicio": None, "paro_automatico": None}),
         {"enable": True, "freq_hz": 20, "direction": "derecha"})

    caso("3. 'cuando S1 detecte sube pluma 1'", "cuando S1 detecte sube la pluma 1",
         _intent(eventos=[_evento(1, pluma1="subir")]),
         {"enable": False, "s1_action": 0, "s1_band_mode": 1, "s1_pluma1": 1, "s1_pluma2": None})

    caso("4. 'cuando S1 detecte sube ambas plumas'", "cuando S1 detecte sube ambas plumas",
         _intent(eventos=[_evento(1, pluma1="subir", pluma2="subir")]),
         {"s1_pluma1": 1, "s1_pluma2": 1})

    caso("5. 'cuando S2 detecte baja ambas plumas'", "cuando S2 detecte baja ambas plumas",
         _intent(eventos=[_evento(2, pluma1="bajar", pluma2="bajar")]),
         {"s2_pluma1": 2, "s2_pluma2": 2})

    caso("6. 'enciende roja cuando S1 detecte'", "enciende la roja cuando S1 detecte",
         _intent(eventos=[_evento(1, luces=["roja"])]),
         {"torreta_s1": 4, "s1_action": 0})

    caso("7. 'prende verde mientras la banda corre'", "prende la verde mientras la banda corre",
         _intent(luces={"corriendo": ["verde"], "detenida": [], "mientras_i1": []}),
         {"torreta_run": 1, "torreta_idle": None})

    caso("8. 'al contar 3 prende roja'", "cuando la banda cuente 3 piezas prende la roja",
         _intent(eventos=[_evento(1, conteo=3, al_contar=_al_contar(luces=["roja"]))]),
         {"count_s1": 3, "s1_count_action_mask": 4, "s1_count_lamp_mask": 4, "torreta_s1": None})

    caso("9. 'detente 5 segundos y continua'",
         "la banda avanza a la derecha a 30 Hz y cuando S1 detecte detente 5 segundos y continua",
         _intent(movimiento={"mover": True, "direccion": "derecha", "frecuencia_hz": 30,
                             "boton_inicio": None, "paro_automatico": None},
                 eventos=[_evento(1, banda="pausa_temporizada", duracion_s=5)]),
         {"enable": True, "freq_hz": 30, "s1_action": 2, "s1_band_mode": 0, "wait_s1_s": 5})

    caso("10. 'enciende lampara de la banda'", "enciende la lampara verde de la banda",
         _intent(luces={"corriendo": [], "detenida": ["verde"], "mientras_i1": []}),
         {"torreta_idle": 1, "enable": False})

    # El evento de cada deteccion NO se pierde por existir un contador (§7).
    print("\n11. evento inmediato + contador en el mismo sensor")
    band, errores = pipeline(_intent(eventos=[
        _evento(1, conteo=3, pluma1="subir",
                al_contar=_al_contar(luces=["roja"], duracion_s=5))]))
    check(not errores, "sin errores", "; ".join(errores))
    check(band["s1_pluma1"] == 1, "la pluma sube en CADA deteccion (s1_pluma1=1)",
          f"dio {band['s1_pluma1']}")
    check(band["count_s1"] == 3, "conteo 3", f"dio {band['count_s1']}")
    check(band["s1_count_lamp_mask"] == 4 and band["s1_count_hold_s"] == 5,
          "al contar: roja 5 s", f"dio {band['s1_count_lamp_mask']}/{band['s1_count_hold_s']}")

    # Una intencion SIN capa semantica sigue siendo valida (compatibilidad).
    print("\n12. intencion sin capa semantica (formato anterior)")
    vieja = _intent(eventos=[_evento(1, pluma1="subir")])
    check(banda_intent.validar_intencion(vieja) == [], "valida sin campos nuevos")
    check(set(banda_intent.capa_semantica(vieja)["archetypes"]) == set(),
          "capa semantica vacia, no inventada")
    check(banda_intent.resumen_semantico(vieja) == [], "sin lectura conceptual que mostrar")

    # La capa semantica no altera la cobertura ni los registros (§15).
    print("\n13. la capa semantica no cambia ni un registro")
    con_capa = dict(vieja)
    con_capa.update({"archetypes": ["positioning", "counting"],
                     "process_reference": {"industry": "food_beverage", "reference": "PepsiCo",
                                           "exact_company_process_claim": False},
                     "supuestos": ["la pluma 1 hace de tope"]})
    b1, e1 = pipeline(vieja)
    b2, e2 = pipeline(con_capa)
    check(b1 == b2, "misma configuracion con y sin capa semantica",
          f"{json.dumps(b1)}\n        {json.dumps(b2)}")
    check(not e2, "sin errores con capa semantica", "; ".join(e2))

    # Las instrucciones del maletin siguen yendo al maletin (§1).
    print("\n14. el router del maletin no cambia")
    for texto in ("usa un enclavamiento con I1 y paro con I2",
                  "haz una secuencia de 3 pasos",
                  "prende Q10 con I1",
                  "pon un contador de 5 en Q11"):
        d = device_router.detectar_dispositivo(texto)["device"]
        check(d == "maletin", f"{texto!r} -> maletin", f"dio {d}")
    for texto in ("enciende una lampara", "activa una salida cuando se presione un boton"):
        d = device_router.detectar_dispositivo(texto)["device"]
        check(d is None, f"{texto!r} -> sigue preguntando", f"dio {d}")


# ===========================================================================
# BLOQUE 2 — CONTEXTO INDUSTRIAL (§20): instrucciones conceptuales
# ===========================================================================
HARDWARE_REAL = set(banda_intent.CAMPOS_BAND)


def sin_hardware_inventado(band):
    """La configuracion solo puede tocar campos del ST (§10)."""
    return set(band) <= HARDWARE_REAL


def contexto_nuevo():
    print("\n" + "=" * 74)
    print("CONTEXTO INDUSTRIAL (§20) — instrucciones conceptuales")
    print("=" * 74)

    # --- "quiero una banda como PepsiCo" ---------------------------------
    intent = _intent(
        process_reference={"industry": "food_beverage",
                           "reference": "linea de bebidas estilo PepsiCo",
                           "exact_company_process_claim": False},
        archetypes=["continuous_transport", "counting", "flow_control", "signaling"],
        movimiento={"mover": True, "direccion": "derecha", "frecuencia_hz": None,
                    "boton_inicio": "I1", "paro_automatico": None},
        eventos=[_evento(1), _evento(2, banda="pausa_mientras_detecta", luces=["amarilla"])],
        luces={"corriendo": ["verde"], "detenida": [], "mientras_i1": []},
        supuestos=["S2 pausa la banda mientras la salida siga ocupada"],
        parametros_faltantes=[{"campo": "frecuencia_hz",
                               "pregunta": "¿A que frecuencia debe avanzar la banda (1 a 327 Hz)?",
                               "opciones": ["20 Hz", "30 Hz", "40 Hz"]}])
    band = caso("1. 'quiero una banda como PepsiCo'", "quiero una banda como PepsiCo", intent,
                {"enable": True, "torreta_run": 1, "s2_action": 3, "s2_band_mode": 0,
                 "torreta_s2": 2}, pendiente="frecuencia")
    sem = banda_intent.capa_semantica(intent)
    check(len(sem["archetypes"]) == 4, "4 arquetipos (no se elige uno solo)", str(sem["archetypes"]))
    check(sem["process_reference"]["exact_company_process_claim"] is False,
          "no se afirma el proceso real de la empresa")
    check(sin_hardware_inventado(band), "solo campos reales del ST")
    p = banda_intent.pregunta_pendiente(intent)
    check(p and p["slot"] == "frecuencia", "pide la frecuencia antes de ejecutar", str(p))

    # --- "quiero clasificacion" ------------------------------------------
    intent = _intent(
        archetypes=["sorting", "counting"],
        movimiento={"mover": True, "direccion": "derecha", "frecuencia_hz": 25,
                    "boton_inicio": "I1", "paro_automatico": None},
        eventos=[_evento(1, pluma1="subir")],
        luces={"corriendo": ["verde"], "detenida": [], "mientras_i1": []},
        unsupported_capability=["no hay camara ni lector: la ruta se elige con la pluma 1"])
    band = caso("2. 'quiero clasificacion'", "quiero clasificacion", intent,
                {"enable": True, "freq_hz": 25, "s1_pluma1": 1})
    check(sin_hardware_inventado(band), "la clasificacion usa una pluma, no un robot")
    avisos = banda_intent.avisos_semanticos(intent)
    check(any("camara" in a for a in avisos), "avisa que no hay camara", str(avisos))
    check(not any("camara" in e for e in pipeline(intent)[1]),
          "la limitacion conceptual avisa pero NO bloquea")

    # --- "quiero un proceso de empaquetado" ------------------------------
    intent = _intent(
        archetypes=["continuous_transport", "counting", "packaging"],
        movimiento={"mover": True, "direccion": "derecha", "frecuencia_hz": 30,
                    "boton_inicio": "I1", "paro_automatico": None},
        eventos=[_evento(1, conteo=6, al_contar=_al_contar(pausar_banda=True, luces=["amarilla"],
                                                           duracion_s=5))],
        luces={"corriendo": ["verde"], "detenida": [], "mientras_i1": []})
    band = caso("3. 'quiero un proceso de empaquetado'", "quiero un proceso de empaquetado", intent,
                {"count_s1": 6, "s1_count_action_mask": 1 + 4, "s1_count_lamp_mask": 2,
                 "s1_count_hold_s": 5})
    check(sin_hardware_inventado(band), "empaque = contar + pausa + señal")

    # --- "quiero una linea por lotes" ------------------------------------
    intent = _intent(
        archetypes=["batching", "counting", "end_of_line"],
        movimiento={"mover": True, "direccion": "derecha", "frecuencia_hz": 20,
                    "boton_inicio": "I1", "paro_automatico": None},
        eventos=[_evento(2, conteo=10, al_contar=_al_contar(detener_proceso=True, luces=["amarilla"]))],
        luces={"corriendo": ["verde"], "detenida": [], "mientras_i1": []})
    caso("4. 'quiero una linea por lotes'", "quiero una linea por lotes", intent,
         {"count_s2": 10, "s2_count_action_mask": 2 + 4, "s2_count_lamp_mask": 2})

    # --- "quiero una banda tipo Amazon" ----------------------------------
    intent = _intent(
        process_reference={"industry": "parcel_distribution", "reference": "clasificacion de paqueteria",
                           "exact_company_process_claim": False},
        archetypes=["continuous_transport", "sorting", "counting", "two_route_split"],
        movimiento={"mover": True, "direccion": "derecha", "frecuencia_hz": 35,
                    "boton_inicio": "I1", "paro_automatico": None},
        eventos=[_evento(1, pluma1="subir"), _evento(2, pluma1="bajar")],
        luces={"corriendo": ["verde"], "detenida": [], "mientras_i1": []},
        unsupported_capability=["no hay lector de codigo de barras: la ruta se decide por sensor"])
    band = caso("5. 'quiero una banda tipo Amazon'", "quiero una banda tipo Amazon", intent,
                {"s1_pluma1": 1, "s2_pluma1": 2})
    check(sin_hardware_inventado(band), "dos rutas con una pluma, sin lector inventado")

    # --- "quiero un buffer" ----------------------------------------------
    intent = _intent(
        archetypes=["buffering", "flow_control", "congestion_control"],
        movimiento={"mover": True, "direccion": "derecha", "frecuencia_hz": 20,
                    "boton_inicio": "I1", "paro_automatico": None},
        eventos=[_evento(2, banda="pausa_mientras_detecta", luces=["amarilla"])],
        luces={"corriendo": ["verde"], "detenida": [], "mientras_i1": []})
    caso("6. 'quiero un buffer'", "quiero un buffer", intent,
         {"s2_action": 3, "s2_band_mode": 0, "torreta_s2": 2})

    # --- "quiero una estacion automotriz" --------------------------------
    intent = _intent(
        process_reference={"industry": "automotive", "reference": None,
                           "exact_company_process_claim": False},
        archetypes=["indexed_transport", "positioning", "two_station_process"],
        movimiento={"mover": True, "direccion": "derecha", "frecuencia_hz": 15,
                    "boton_inicio": "I1", "paro_automatico": None},
        eventos=[_evento(1, banda="pausa_temporizada", duracion_s=8, luces=["amarilla"],
                         pluma1="bajar"),
                 _evento(2, banda="pausa_temporizada", duracion_s=8, luces=["amarilla"],
                         pluma2="bajar")],
        luces={"corriendo": ["verde"], "detenida": [], "mientras_i1": []},
        unsupported_capability=["no hay robot ni herramienta: la estacion se simula con una pausa"])
    band = caso("7. 'quiero una estacion automotriz'", "quiero una estacion automotriz", intent,
                {"s1_action": 4, "wait_s1_s": 8, "s1_pluma1": 2,
                 "s2_action": 4, "wait_s2_s": 8, "s2_pluma2": 2})
    check(sin_hardware_inventado(band), "estacion = pausa + tope + señal, sin robot")

    # --- "quiero contar cajas para un pallet" -----------------------------
    intent = _intent(
        process_reference={"industry": "warehouse_logistics", "reference": None,
                           "exact_company_process_claim": False},
        archetypes=["counting", "batching", "pallet_feed"],
        movimiento={"mover": True, "direccion": "derecha", "frecuencia_hz": None,
                    "boton_inicio": "I1", "paro_automatico": None},
        eventos=[_evento(1, al_contar=_al_contar(pausar_banda=True, luces=["amarilla"]))],
        luces={"corriendo": ["verde"], "detenida": [], "mientras_i1": []},
        unsupported_capability=["no hay robot paletizador: el pallet listo solo se señaliza"],
        parametros_faltantes=[
            {"campo": "conteo", "pregunta": "¿Cuantas cajas forman el pallet?",
             "opciones": ["5", "10", "20"]},
            {"campo": "frecuencia_hz", "pregunta": "¿A que frecuencia debe avanzar la banda?",
             "opciones": ["20 Hz"]}])
    print("\n8. 'quiero contar cajas para un pallet' (faltan datos: NO se ejecuta)")
    d = device_router.detectar_dispositivo("quiero contar cajas para un pallet")["device"]
    check(d == "banda", "router -> banda", f"dio {d}")
    band, errores = pipeline(intent)
    check(any("conteo" in e for e in errores),
          "sin el numero de cajas la configuracion NO pasa", "; ".join(errores))
    p = banda_intent.pregunta_pendiente(intent)
    check(p and p["slot"] == "frecuencia", "se pregunta antes de ejecutar", str(p))
    sem = banda_intent.capa_semantica(intent)
    check([x["campo"] for x in sem["parametros_faltantes"]] == ["conteo", "frecuencia_hz"],
          "los dos parametros quedan marcados como pendientes", str(sem["parametros_faltantes"]))

    # --- composicion de procesos (§5) -------------------------------------
    print("\n9. composicion: 'transporta, cuenta 5 y clasifica'")
    intent = _intent(
        archetypes=["continuous_transport", "counting", "sorting"],
        movimiento={"mover": True, "direccion": "derecha", "frecuencia_hz": 25,
                    "boton_inicio": "I1", "paro_automatico": None},
        eventos=[_evento(1, conteo=5, al_contar=_al_contar(pluma1="subir"))])
    band, errores = pipeline(intent)
    check(not errores, "sin errores", "; ".join(errores))
    check(banda_intent.capa_semantica(intent)["archetypes"] ==
          ["continuous_transport", "counting", "sorting"], "los TRES arquetipos, no uno solo")
    check(band["s1_count_action_mask"] == 16 and band["s1_count_pluma1"] == 1,
          "al contar 5 la pluma 1 desvia", str(band["s1_count_action_mask"]))

    # --- conflictos (§14) --------------------------------------------------
    print("\n10. conflicto en el mismo evento")
    intent = _intent(eventos=[_evento(1, pluma1="subir")],
                     conflictos=["S1 sube y baja la pluma 1 en el mismo evento"])
    p = banda_intent.pregunta_conflicto(intent)
    check(p is not None and "pluma 1" in p["pregunta"], "se pregunta, no se resuelve solo", str(p))
    print("     eventos distintos NO son conflicto:")
    band, errores = pipeline(_intent(eventos=[_evento(1, pluma1="subir"), _evento(2, pluma1="bajar")]))
    check(not errores, "S1 sube / S2 baja la pluma 1 es valido", "; ".join(errores))
    print("     dos ordenes de la misma pluma en el MISMO sensor si son conflicto:")
    _, errores = pipeline(_intent(eventos=[_evento(1, pluma1="subir"), _evento(1, pluma1="bajar")]))
    check(any("pluma 1" in e for e in errores), "se reporta, no se elige una", "; ".join(errores))

    # --- referencias de empresa (§12) --------------------------------------
    print("\n11. una referencia de empresa nunca se presenta como el proceso real")
    intent = _intent(process_reference={"industry": "food_beverage", "reference": "Coca-Cola",
                                        "exact_company_process_claim": True})
    errores = banda_intent.validar_intencion(intent)
    check(any("referencia conceptual" in e for e in errores),
          "se rechaza afirmar el proceso real de la empresa", "; ".join(errores))
    check(banda_intent.capa_semantica(intent)["process_reference"]
          ["exact_company_process_claim"] is False, "la capa normalizada siempre lo deja en false")

    # --- arquetipo inventado (§5) ------------------------------------------
    print("\n12. no se aceptan arquetipos inventados")
    errores = banda_intent.validar_intencion(_intent(archetypes=["teletransporte"]))
    check(any("arquetipo" in e for e in errores), "'teletransporte' rechazado", "; ".join(errores))

    # --- hardware inventado pedido por el usuario (§10) --------------------
    print("\n13. hardware que el usuario pide y no existe: sigue bloqueando")
    _, errores = pipeline(_intent(no_soportado=["una camara para inspeccionar el producto"]))
    check(any("camara" in e for e in errores), "'no_soportado' bloquea como siempre",
          "; ".join(errores))


# ===========================================================================
# BLOQUE 3 — LAS DOS CAPAS DE CONTEXTO (§21, §22)
# ===========================================================================
def capas():
    print("\n" + "=" * 74)
    print("FUENTES DE CONTEXTO (§21, §22)")
    print("=" * 74)
    import app

    prompt_a = app.SYSTEM_PROMPT_BANDA
    bloque_b = banda_contexto.bloque_industrial_prompt()

    check(banda_contexto.contexto_disponible(),
          "el archivo de contexto industrial se carga desde context/")
    check("HARDWARE DE LA BANDA" in prompt_a and "al_contar" in prompt_a,
          "la capa A (prompt anterior) sigue completa")
    check("ARQUETIPOS DE PROCESO" in bloque_b and "REFERENCIAS POR INDUSTRIA" in bloque_b,
          "la capa B trae arquetipos e industrias")
    check(prompt_a not in bloque_b and bloque_b not in prompt_a,
          "son dos mensajes distintos: ninguno reemplaza al otro")
    check("no_soportado" in bloque_b and "unsupported_capability" in bloque_b,
          "la capa B distingue lo que bloquea de lo que solo avisa")
    check("capacidades fisicas reales" in bloque_b,
          "la capa B declara que no sobrescribe una limitacion fisica")
    for a in ("sorting", "batching", "pallet_feed", "two_route_split", "end_of_line"):
        check(a in bloque_b, f"arquetipo '{a}' presente en el contexto")

    # Un fallo al leer el archivo no puede tumbar la banda.
    texto = banda_contexto.cargar_contexto_industrial("no/existe/contexto.txt", recargar=True)
    check("Arquetipos de proceso disponibles" in texto,
          "sin archivo se usa el resumen minimo, no se cae")
    banda_contexto.cargar_contexto_industrial(recargar=True)   # restaurar cache

    # El mensaje de la capa B es ADICIONAL, va detras del de siempre.
    msg = banda_contexto.mensaje_sistema_industrial()
    check(msg["role"] == "system" and "CAPA B" in msg["content"],
          "la capa B viaja como mensaje de sistema adicional")


# ===========================================================================
# MODO LIVE (opcional): prueba el LLM de verdad si hay GROQ_API_KEY
# ===========================================================================
LIVE_REGRESION = [
    "avanza a la derecha",
    "avanza a 20 Hz",
    "cuando S1 detecte sube la pluma 1",
    "cuando S1 detecte sube ambas plumas",
    "cuando S2 detecte baja ambas plumas",
    "enciende la roja cuando S1 detecte",
    "prende la verde mientras la banda corre",
    "en la banda, al contar 3 prende la roja",
    "la banda avanza a 30 Hz y cuando S1 detecte detente 5 segundos y continua",
    "enciende la lampara verde de la banda",
]
LIVE_NUEVAS = [
    "quiero una banda como PepsiCo",
    "quiero clasificacion",
    "quiero un proceso de empaquetado",
    "quiero una linea por lotes",
    "quiero una banda tipo Amazon",
    "quiero un buffer",
    "quiero una estacion automotriz",
    "quiero contar cajas para un pallet",
]


def live():
    import os
    if not os.environ.get("GROQ_API_KEY"):
        print("\n(--live omitido: no hay GROQ_API_KEY en el entorno)")
        return
    import app
    from app import LogicaRequest
    print("\n" + "=" * 74)
    print("MODO LIVE — el LLM de verdad")
    print("=" * 74)
    for texto in LIVE_REGRESION + LIVE_NUEVAS:
        print(f"\n> {texto!r}")
        try:
            r = app._generar_logica_banda(texto, LogicaRequest(texto=texto, device="banda"))
        except Exception as e:
            check(False, f"live: {texto!r}", str(e))
            continue
        if getattr(r, "status", "") == "needs_clarification":
            print("  pregunta:", r.questions[0]["pregunta"])
            check(True, f"live: {texto!r} -> pregunta en vez de inventar")
            continue
        band = r.logic["band"]
        sem = r.logic.get("semantica", {})
        print("  arquetipos:", sem.get("archetypes"))
        for linea in banda_intent.resumen_acciones(r.logic["intent"]):
            print("   -", linea)
        check(set(band) <= HARDWARE_REAL, f"live: {texto!r} sin hardware inventado")


# ===========================================================================
if __name__ == "__main__":
    regresion()
    contexto_nuevo()
    capas()
    if "--live" in sys.argv:
        live()
    print("\n" + "=" * 74)
    if FALLOS:
        print(f"{len(FALLOS)} FALLO(S) de {PRUEBAS[0]} comprobaciones:")
        for f in FALLOS:
            print("  -", f)
        sys.exit(1)
    print(f"TODAS LAS PRUEBAS PASARON ({PRUEBAS[0]} comprobaciones).")
