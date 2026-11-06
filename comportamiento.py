"""
comportamiento.py — ¿la logica HACE lo que pidio el usuario? (banda v7)

validar_logica_banda comprueba que el JSON se puede cargar; aqui se comprueba
el COMPORTAMIENTO: se corre el texto ST real del programa maestro v7 en el
simulador (lavo_sim, scans de 10 ms) y se prueban los comportamientos que se
pueden leer de las instrucciones de la sesion:

  arranca_con  "arranca con I1", "al presionar I1 avanza"
  para_con     "con I2 se detiene", "I2 la para"
  para_sola    "haz que se detenga por si sola", "que se pare sola a los 10 s",
               "que se detenga sola cuando S1 detecte"

  requisitos(textos)       -> lo que se puede probar (de todas las instrucciones)
  verificar(cfg, reqs)     -> [] si cumple; si no, que falla (en palabras)
  paro_solo_sin_condicion  -> pide paro automatico sin decir cuando (se pregunta)
  pregunta_paro_solo()     -> pregunta con opciones de un clic

Solo se prueba lo que el texto dice sin ambiguedad: un requisito que no se
reconoce no se inventa (el resto lo sigue revisando el validador de siempre).
"""

import re
import unicodedata

import cache_simulacion
import cambios_texto
import lavo_motor as lm

# Tope de tiempo simulado por prueba (s): sin condicion dicha, la banda debe
# detenerse sola antes de esto.
MAX_SOLA_S = 45
# Tope de tiempo REAL para todas las pruebas de una respuesta (s): lo que no
# alcance a probarse se omite (no se reporta como falla).
PRESUPUESTO_S = 15
_ARRANQUE_S = 2.0       # marcha tras arrancar antes de probar
_FRENO_S = 1.5          # lo que tarda en verse el paro (rampa del VFD)

_NUM = {"un": 1, "una": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6,
        "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "quince": 15, "veinte": 20, "treinta": 30}
_NUM_RX = r"\d+(?:[.,]\d+)?|" + "|".join(sorted(_NUM, key=len, reverse=True))


# Errores de escritura frecuentes (dictado, teclado de los lentes) que cambian el sentido:
# "se detenga por sis ola" -> "por si sola".
_TYPOS = [(re.compile(r"\bsis\s+ola\b"), "si sola"), (re.compile(r"\bporsi\b"), "por si"),
          (re.compile(r"\bpor\s+sis\b"), "por si"), (re.compile(r"\bsi\s+sola\b"), "si sola"),
          (re.compile(r"\bbotn\b"), "boton"), (re.compile(r"\bsensr\b"), "sensor")]


def _fold(texto) -> str:
    t = unicodedata.normalize("NFD", str(texto or "").lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    t = re.sub(r"\s+", " ", t)
    for rx, bien in _TYPOS:
        t = rx.sub(bien, t)
    return t


def _numero(s) -> float:
    s = str(s).replace(",", ".")
    return float(_NUM.get(s, s))


def _senal(s: str):
    s = (s or "").replace(" ", "")
    s = {"sensor1": "S1", "sensoruno": "S1", "primersensor": "S1", "sensor2": "S2", "sensordos": "S2",
         "segundosensor": "S2", "botonverde": "I1", "botonrojo": "I2", "verde": "I1", "rojo": "I2",
         "i4": "S1", "i5": "S2"}.get(s, s)
    s = s.upper()
    return s if re.fullmatch(r"[IS]\d", s) else None


# ── Lectura de las instrucciones ───────────────────────────────────────────
_VERBO_PARO = r"(?:deten\w*|deteng\w*|detien\w*|par[ae]\w*|frene\w*|fren[ae]\w*)"
_SOLA = (r"(?:por si (?:sola|solo|misma|mismo)|sola|solita|automatic\w*|en automatico|"
         r"sin (?:que (?:yo |nadie |el usuario )?(?:la |lo )?(?:toque|presione|pulse|apriete|haga nada|intervenga|"
         r"detenga)\w*|intervencion\w*|presionar|pulsar|tocar|apretar)(?: (?:nada|ningun boton|botones))?)")
_RX_PARO_SOLA = re.compile(rf"\b{_VERBO_PARO}\b[^.;]{{0,40}}?\b{_SOLA}|\b{_SOLA}\b[^.;]{{0,25}}?\b{_VERBO_PARO}\b|"
                           r"\bparo automatico\b|\bauto ?paro\b")
_RX_SIN_PARO_SOLA = re.compile(rf"\b(?:ya no|quita\w*|elimina\w*|sin)\b[^.;]{{0,30}}(?:paro automatico|"
                               rf"{_VERBO_PARO}[^.;]{{0,15}}\b(?:sola|por si sola))")
_RX_TIEMPO = re.compile(rf"\b(?:a los|despues de|tras|luego de|en|pasados?|al cabo de|cada|durante)?\s*"
                        rf"({_NUM_RX})\s*(s|seg|segs|segundos?|min|minutos?)\b")
_RX_SENSOR = re.compile(r"\b(?:cuando|al|en cuanto|si)\s+(?:el |la )?(?:sensor\s*)?(s ?1|s ?2|sensor (?:1|2|uno|dos)|"
                        r"primer sensor|segundo sensor)\b|\b(s ?1|s ?2|sensor (?:1|2|uno|dos))\s+(?:detect\w*|vea|lea)")
_RX_CONTEO = re.compile(rf"\b(?:cuente|contar|cuenta|cont\w*|llegue a|lleguen|despues de|al pasar|pasen)\s+"
                        rf"({_NUM_RX})\s+(?:cajas|piezas|objetos|productos|detecciones)\b")
# Entre el boton y el verbo no puede haber OTRO boton ("arranca con I1 y con I2 se detiene").
# Las comas SI se permiten ("al presionar el boton verde, la banda comienza a avanzar"):
# antes una coma bastaba para que el arranque no se reconociera ni se probara.
_SIN_OTRO = r"(?:(?!\b(?:con|i ?\d|s ?\d|boton|y|e|o)\b)[^.;])"
# Alternancia con el mismo boton: "al presionarlo por segunda vez se detiene", "si lo vuelvo a
# presionar se para", "con el mismo boton se detiene", "que el boton verde alterne".
_RX_TOGGLE = re.compile(r"\b(?:presion\w*|puls\w*|oprim\w*|apriet\w*|tocar\w*|toque\w*)\s+(?:por\s+|una\s+)?"
                        r"(?:segunda vez|otra vez|de nuevo|nuevamente)|\bvuelv\w* a (?:presion\w*|puls\w*|oprim\w*|"
                        r"apretar\w*)|\bvolver a (?:presion\w*|puls\w*|oprim\w*)|\b(?:mismo|misma) (?:boton|pulsador|"
                        r"tecla)\b|\b(?:altern\w*|toggle|conmut\w*)\b")
_RX_TOGGLE_PARO = re.compile(rf"\b{_VERBO_PARO}|\bapag\w*")
_RX_BOTON_TEXTO = re.compile(r"\b(i ?[12]|boton verde|boton rojo)\b")
# "al presionar I1, la banda avanza 10 s, se detiene 3 s" es una SECUENCIA que arranca con
# I1, no un paro con I1: entre el boton y el paro no puede haber un verbo de marcha.
_SIN_OTRO_NI_MARCHA = (r"(?:(?!\b(?:con|i ?\d|s ?\d|boton|y|e|o|avanz\w*|avanc\w*|arranc\w*|marcha|muev\w*|"
                       r"pone|ponga)\b)[^.;])")
_RX_PARO_BOTON = re.compile(rf"\b(?:con|al (?:presionar|pulsar|oprimir)|presionando|pulsando|cuando (?:presione|pulse))\s+"
                            rf"(?:el |la )?(?:boton |pulsador )?(i ?[12]|boton rojo|boton verde|rojo|verde)\b"
                            rf"{_SIN_OTRO_NI_MARCHA}{{0,40}}?\b"
                            rf"{_VERBO_PARO}|\b(i ?[12])\s+(?:la |lo )?(?:detiene|para|apaga|frena)\b")
_RX_ARRANQUE_BOTON = re.compile(rf"\b(?:con|al (?:presionar|pulsar|oprimir)|presionando|pulsando|cuando (?:presione|pulse))\s+"
                                rf"(?:el |la )?(?:boton |pulsador )?(i ?[12]|boton verde|boton rojo)\b{_SIN_OTRO}{{0,40}}?\b"
                                r"(?:arranc\w*|avanc\w*|avanz\w*|muev\w*|se pone en marcha)|"
                                r"\b(?:arranca\w*|avanz\w*|avanc\w*|mover|mueve)\s+(?:la banda\s+|la cinta\s+)?"
                                r"con (?:el |la )?(?:boton |pulsador )?(i ?[12])\b")


# "Cuando S2 cuente 3 cajas prende la roja 5 s y que el contador se reinicie solo",
# "cada 3 piezas en S1 sube la pluma", "al llegar a 4 detecciones ...".
_NO_TIEMPO = r"(?!\s*(?:[.,]\d|s\b|seg|segundos?|min|minutos?|hz|hertz|%|veces por))"
_RX_CUENTA_N = re.compile(rf"\b(?:cuente\w*|contar|cuenta|cont[oae]\w*|llegu\w* a|llegar a|lleg[ao] a|acumul\w*|"
                          rf"complet\w*|registr\w*|cada|despues de|al pasar|pasen)\s+(?:a\s+)?"
                          # "llegue a un contador de 3", "llegue a un conteo de 3": el "un" no es el numero
                          rf"(?:(?:un|el)\s+(?:contador|conteo|total|numero|valor)\s+de\s+)?({_NUM_RX})\b{_NO_TIEMPO}"
                          rf"(?:\s+(cajas|piezas|objetos|productos|detecciones|conteos|pulsos))?")
_RX_HAY_CONTEO = re.compile(r"\b(contador\w*|conteo\w*|cuent\w*|cont[ae]\w*|acumul\w*|detecciones|cajas|piezas|"
                            r"objetos|productos)\b")
# Se reinicia SOLO (vuelve a contar). "se reinicia con I2 / al presionar" es manual.
_RX_REINICIA_SOLO = re.compile(r"\b(?:reinici\w*|resete\w*|reestable\w*|vuelv\w* a (?:contar|cero|0|empezar|iniciar)|"
                               r"(?:empie|comien)\w* de nuevo|de nuevo|otra vez|nuevamente|se repit\w*|"
                               r"vuelv\w* a cont\w*)\b|\bcada (?=\d|un|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez)")
# Reset con boton: "se reinicia con I2", "al presionar el boton verde se borra la cuenta",
# "con I1 se resetea el contador". I3 es el paro de emergencia: no cuenta.
_BOTON = r"(?:el |la )?(?:boton |pulsador )?(i ?[123]|boton verde|boton rojo|verde|rojo)\b"
_VERBO_RESET = r"(?:reinici\w*|resete\w*|reset\b|borr\w*|limpi\w*|pon\w* (?:en|a) (?:cero|0)|vuelv\w* a (?:cero|0))"
_RX_RESET_BOTON = re.compile(rf"\b{_VERBO_RESET}[^.;]{{0,30}}?\b(?:con|al (?:presionar|pulsar|oprimir)|presionando|"
                             rf"pulsando|cuando (?:presione|pulse))\s+{_BOTON}|"
                             rf"\b(?:con|al (?:presionar|pulsar|oprimir)|presionando|pulsando|cuando (?:presione|pulse))"
                             rf"\s+{_BOTON}[^.;]{{0,25}}?\b(?:se )?{_VERBO_RESET}[^.;]{{0,15}}\b(?:contador|conteo|cuenta)")
# Reinicio automatico dicho explicitamente (aunque tambien haya boton).
_RX_REINICIA_AUTO = re.compile(r"\b(?:reinici\w*|resete\w*|vuelv\w* a (?:cero|0|contar|empezar))\s+(?:el contador\s+)?"
                               r"(?:solo|sola|solito|por si (?:solo|sola|mismo)|automatic\w*|en automatico)\b|"
                               r"\b(?:solo|automatic\w*)\s+(?:se\s+)?(?:reinici\w*|resete\w*)")
_RX_SIN_CONTEO = re.compile(r"\b(?:quita\w*|elimina\w*|borra\w*|ya no)\b[^.;]{0,20}\b(?:el contador|contador|conteo|"
                            r"cuente|contar)\b")
_RX_SENSOR_CUENTA = re.compile(r"\b(s ?1|s ?2|sensor (?:1|2|uno|dos)|primer sensor|segundo sensor)\b")


def conteo_en(texto: str) -> dict:
    """{"conteo": N, "senal": S1|S2, "reinicia": bool} si el texto pide contar
    N objetos en un sensor (o {} si no lo dice sin ambiguedad)."""
    t = _fold(texto)
    m = _RX_CUENTA_N.search(t)
    if not m or not (m.group(2) or _RX_HAY_CONTEO.search(t)):
        return {}
    n = int(_numero(m.group(1)))
    if not 1 < n <= 20:
        return {}
    # El sensor que cuenta: el mas cercano antes de "cuente N" (o, si no, el primero que se nombre).
    previos = [x for x in _RX_SENSOR_CUENTA.finditer(t[:m.start()])]
    s = _senal((previos[-1] if previos else _RX_SENSOR_CUENTA.search(t) or m).group(1)) \
        if (previos or _RX_SENSOR_CUENTA.search(t)) else None
    if s not in ("S1", "S2"):
        return {}
    # reinicia = vuelve a cero SOLO; boton = tambien (o solo) con ese boton.
    mb = _RX_RESET_BOTON.search(t)
    boton = _senal(next(g for g in mb.groups() if g)) if mb else None
    sin_boton = _RX_RESET_BOTON.sub(" ", t) if mb else t
    reinicia = bool(_RX_REINICIA_AUTO.search(t)) or bool(_RX_REINICIA_SOLO.search(sin_boton))
    out = {"conteo": n, "senal": s, "reinicia": reinicia, "boton": boton if boton in ("I1", "I2") else None}
    if boton == "I3":
        out["boton_invalido"] = "I3"          # paro de emergencia: no puede reiniciar el contador
    return out


_RX_CTX_BANDA = re.compile(r"\b(banda|cinta|transportadora|motor|hz|hertz)\b")
_RX_CTX_OTRO = re.compile(r"\b(luz|luces|lampara\w*|torreta|verde|amarill\w*|roj[ao]s?|pluma\w*|secuencia\w*|"
                          r"semaforo|q\d+)\b")


def _oraciones(texto: str) -> list:
    return [o for o in re.split(r"[.;\n]+", _fold(texto)) if o.strip()]


def condicion_paro_solo(texto: str) -> dict:
    """{"segundos"|"senal"|"conteo": ...} dicho en el texto (o {} si no lo dice)."""
    t = _fold(texto)
    m = _RX_CONTEO.search(t)
    if m:
        return {"conteo": int(_numero(m.group(1))), "senal": "S2" if "s1" not in t and "sensor 1" not in t else "S1"}
    m = _RX_SENSOR.search(t)
    if m:
        return {"senal": _senal(m.group(1) or m.group(2))}
    m = _RX_TIEMPO.search(t)
    if m:
        n = _numero(m.group(1))
        return {"segundos": n * 60 if m.group(2).startswith("min") else n}
    return {}


def pide_paro_solo(texto: str) -> bool:
    return bool(_RX_PARO_SOLA.search(_fold(texto)))


# Paro ligado a un conteo: "cuando el contador llegue a 3, la banda avanza 5 s y luego
# se detiene (sola)". Lo dispara el conteo, no un boton: basta "se detiene sola",
# "se detiene", "se para" o "para" (como verbo, no "para que" / "para contar").
_RX_PARO_CONTEO = re.compile(
    r"\b(?:se\s+)?(?:deten\w*|detien\w*|deteng\w*)\b|"
    r"\bse\s+par(?:a|e|en|an|ara|ará)\b|"
    r"\b(?:banda|cinta|motor|transportadora|y|luego|despues|entonces|finalmente|ahi|alli)\s+(?:se\s+)?"
    r"par(?:a|e|en|an)\b(?!\s+(?:que|el|la|los|las|un|una|contar|ver|poder|esto|eso|cuando|cada|ello)\b)")
# El paro es de un boton, no del conteo ("con I2 se detiene").
_RX_PARO_DE_BOTON = re.compile(r"\b(?:con|al (?:presionar|pulsar|oprimir)|presionando|pulsando|cuando (?:presione|pulse))"
                               r"\s+(?:el |la )?(?:boton |pulsador )?(?:i ?\d|boton|verde|rojo)")
_RX_PARO_NEGADO = re.compile(r"\b(?:sin|nunca|jamas|ya no|no)\s+(?:que\s+)?(?:se\s+)?(?:la\s+|lo\s+)?$")

# Cambios sobre lo ya pedido (la instruccion mas reciente manda sobre el historial):
# "cambia el conteo a 5", "5 cajas en lugar de 3", "en vez de 3 que sean 5".
_RX_CAMBIA_CONTEO = re.compile(
    rf"\b(?:conteo|contador|cuenta|preset|limite)\b[^.;]{{0,40}}?\b(?:a|en|de|sea|sean)\s+({_NUM_RX})\b{_NO_TIEMPO}|"
    rf"\b({_NUM_RX})\s+(?:cajas|piezas|objetos|productos|detecciones|conteos)?\s*en (?:lugar|vez) de\s+(?:{_NUM_RX})\b|"
    rf"\ben (?:lugar|vez) de\s+(?:{_NUM_RX})\s+(?:cajas|piezas|objetos|productos|detecciones|conteos)?\s*,?\s*"
    rf"(?:que sean|sean|a|que llegue a)\s+({_NUM_RX})\b{_NO_TIEMPO}")
_RX_VERBO_CAMBIO = re.compile(r"\b(?:cambi\w*|pon\w*|ajust\w*|modific\w*|aument\w*|reduc\w*|que sea\w*|en (?:lugar|vez) de)\b")
# "ya no se detiene", "que no se detenga", "sin detenerse": QUITA el paro automatico.
# ("no se detiene" solo, en presente, es una queja: pide que SI se detenga.)
_RX_YA_NO_PARA = re.compile(r"\bya no (?:se |la |lo )?(?:deten\w*|detien\w*|deteng\w*|par[ae]n?)\b|"
                            r"\bque no se (?:detenga|pare)\b|\bsin (?:que se )?deten\w*|\bsin parar(?:se)?\b|"
                            r"\bno (?:debe|tiene que|necesita) (?:detenerse|parar\w*)\b")


# "la luz roja parpadea", "que la amarilla parpadee 4 s cuando S2 detecte".
_RX_PARPADEA = re.compile(r"\b(?:parpade\w*|intermit\w*|destell\w*)\b")
_Q_COLOR = {"verde": "Q3", "amarilla": "Q4", "amarillo": "Q4", "roja": "Q5", "rojo": "Q5"}


def parpadeo_en(texto: str) -> list:
    """[{"q": Q, "senal": S1|S2|I1|I2|None}] de cada oracion que pide que una luz parpadee."""
    out = []
    for o in _oraciones(texto):
        if not _RX_PARPADEA.search(o):
            continue
        cs = re.findall(r"\b(verde|amarill[ao]|roj[ao])\b", o)
        if not cs:
            continue
        ms = re.search(r"\b(?:sensor\s*|s\s?)([12])\b", o)
        mb = re.search(r"\b(?:presion\w*|puls\w*|con)\s+(?:el\s+)?(?:boton\s+)?(i\s?[12])\b", o)
        senal = f"S{ms.group(1)}" if ms else (_senal(mb.group(1)) if mb else None)
        out.append({"q": _Q_COLOR[cs[-1]], "senal": senal})
    return out


# "Esto se repite 3 veces y despues la banda se detiene" (con un sensor que dispara cada vuelta).
_RX_REPITE_PARA = re.compile(
    r"\brepit\w*\s+(?:el ciclo\s+|la secuencia\s+)?(\d+|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez)\s+veces"
    r"[^.;]{0,40}?\b(?:despues|luego|al final|al terminar|y)\b[^.;]{0,30}?\b(?:deten\w*|detien\w*|deteng\w*|para|pare)\b")


def repite_y_para(texto: str) -> dict:
    """{"veces": N, "senal": S1|S2} si pide repetir un ciclo N veces (cada vuelta la
    dispara un sensor) y DESPUES quedar detenida; {} si no."""
    t = _fold(texto)
    m = _RX_REPITE_PARA.search(t)
    if not m:
        return {}
    n = int(_numero(m.group(1)))
    ms = re.search(r"\b(?:sensor\s*|s\s?)([12])\b", t[:m.start()])
    if not ms or not 1 < n <= 10:
        return {}
    return {"veces": n, "senal": f"S{ms.group(1)}"}


# "cuando el sensor 2 detecta una caja, la banda se detiene 4 segundos y luego continua",
# "hasta que S2 detecta; entonces se detiene 4 s, la pluma sube y vuelve a avanzar".
_RX_PAUSA_SENSOR = re.compile(
    r"\b(?:sensor\s*|s\s?)([12])\b[^.]{0,80}?\b(?:se\s+)?(?:deten\w*|detien\w*|deteng\w*|para|pare|pausa\w*)\s+"
    r"(?:durante\s+|por\s+)?(\d+(?:[.,]\d+)?|un|una|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez)\s*"
    r"(?:s|seg|segundos?)\b[^.;]{0,90}?\b(?:vuelv\w* a (?:avanzar|moverse|arrancar)|continu\w*|sigue|siga|"
    r"reanud\w*|retom\w*)")


# "Cada que el sensor uno detecte se espere 3 segundos antes de avanzar".
_RX_PAUSA_ESPERA = re.compile(
    r"\b(?:sensor\s*|s\s?)([12]|uno|dos)\b[^.]{0,60}?\b(?:se\s+)?(?:esper\w*|aguard\w*)\s+"
    r"(?:durante\s+|por\s+)?(\d+(?:[.,]\d+)?|un|una|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez)\s*"
    r"(?:s|seg|segundos?)\b[^.;]{0,30}?\b(?:antes de|y (?:luego|despues)?\s*)\s*(?:volver a\s+)?"
    r"(?:avanz\w*|avanc\w*|continu\w*|segui\w*|sig\w*|arranc\w*|mover\w*)")


def pausa_por_deteccion(texto: str) -> dict:
    """{"senal": S1|S2, "segundos": N} si pide que, cada vez que el sensor detecta, la
    banda se detenga N s y luego siga sola; {} si no."""
    t = _fold(texto)
    m = _RX_PAUSA_SENSOR.search(t)
    if not m:
        m = _RX_PAUSA_ESPERA.search(t)
        if m:
            n = _numero(m.group(2))
            s = {"uno": "1", "dos": "2"}.get(m.group(1), m.group(1))
            return {"senal": f"S{s}", "segundos": n} if 0 < n <= 60 else {}
    if not m:
        return {}
    n = _numero(m.group(2))
    return {"senal": f"S{m.group(1)}", "segundos": n} if 0 < n <= 60 else {}


def _probar_pausa(s, r) -> list:
    """Con la banda en marcha, cada objeto en el sensor: se detiene enseguida, sigue
    detenida casi todo el tiempo pedido y luego vuelve a avanzar sola."""
    n, senal = float(r["segundos"]), r["senal"]
    sensor = "I4" if senal == "S1" else "I5"
    for k in range(1, int(r.get("ciclos") or 2) + 1):
        if not _corre(s):
            return [f"Antes de la deteccion {k} en {senal} la banda no esta avanzando."]
        s.pulsar(sensor, 0.3)
        s.correr(_FRENO_S)
        if _corre(s):
            return [f"Al detectar {senal} (vez {k}) la banda NO se detiene {_fmt(n)} s."]
        if n > 2.5:
            s.correr(n - 2.5)
            if _corre(s):
                return [f"Al detectar {senal} (vez {k}) la banda vuelve a avanzar antes de los {_fmt(n)} s."]
        t = 0.0
        while not _corre(s) and t < 6:
            s.correr(0.5)
            t += 0.5
        if not _corre(s):
            return [f"Al detectar {senal} (vez {k}) la banda se detiene pero NO vuelve a avanzar a los {_fmt(n)} s "
                    f"(la pausa debe durar {_fmt(n)} s en CADA deteccion y luego seguir sola)."]
        s.correr(1.5)
    return []


def cambio_conteo(texto: str):
    """N si el texto CAMBIA el numero de un conteo ya pedido ('cambia el conteo a 5');
    None si no."""
    t = _fold(texto)
    if not _RX_VERBO_CAMBIO.search(t):
        return None
    m = _RX_CAMBIA_CONTEO.search(t)
    if not m:
        return None
    n = int(_numero(next(g for g in m.groups() if g)))
    return n if 1 < n <= 20 else None


def quita_paro(texto: str) -> bool:
    """'ya no se detiene' QUITA el paro automatico, salvo que la misma frase siga
    hablando de un paro o un tiempo despues ("en la tercera ya no se detiene, pasa
    directo a los 5 segundos": cambia una pausa, el paro final sigue)."""
    t = _fold(texto)
    m = _RX_YA_NO_PARA.search(t)
    if not m:
        return False
    # En una queja ("revisa, ya no se detiene a las 3") pide que SI se detenga.
    if re.search(r"\b(?:revis\w*|no funciona\w*|esta mal|falla\w*|corrig\w*|arregl\w*|sigue sin|deberia)\b", t):
        return False
    despues = t[m.end():]
    if _RX_TIEMPO.search(despues) or re.search(r"\b(?:pasa directo|directo a)\b", despues) or \
            _RX_PARO_SOLA.search(despues) or re.search(rf"\b{_VERBO_PARO}\b", despues):
        return False
    return True


# Si nadie dice con que arranca, la banda arranca con I1 (la regla del prompt y de
# reparaciones_banda); un arranque por sensor o "siempre" no se toca.
_RX_AVANZA = re.compile(r"\b(avanc\w*|avanz\w*|se mueva|muev\w*|pone en marcha|ponga en marcha|arranc\w*|arranqu\w*)\b")
_RX_ARRANQUE_OTRO = re.compile(r"\b(?:i ?[12]|boton|pulsador|siempre (?:en marcha|avanzando|encendida|activa)|"
                               r"(?:avance|avanza|corra|corre|gire|gira|funcione|este en marcha) siempre|al encender|"
                               r"al iniciar|al cargar|automaticamente al)\b|"
                               # "cuando S1 detecte, la banda arranca": arranque por sensor (no "acumule 3... continue")
                               r"\b(?:cuando|al|si|en cuanto)\s+(?:el |la )?(?:sensor\s*|s ?)[12]"
                               r"(?:(?!acumul|cuent|cont[aeo]|detecciones|continu|sigue|siga)[^.;]){0,50}"
                               r"\b(?:avanc\w*|avanz\w*|arranc\w*|marcha|muev\w*|invierte|invertir)")


def paro_por_conteo(texto: str) -> dict:
    """{"conteo": N, "senal": S1|S2, "segundos": X} si el texto pide que, al llegar el
    contador a N, la banda (siga X s y) se detenga sola; {} si no lo dice.

    El paro debe venir DESPUES del conteo, en la misma oracion o en la siguiente, y
    no ser de un boton. X es el tiempo que dice entre el conteo y el paro ("avanza
    5 segundos y luego se detiene"); 0 si se detiene al llegar."""
    t = _fold(texto)
    c = conteo_en(texto)
    m = _RX_CUENTA_N.search(t)
    if not c or not m:
        return {}
    resto = t[m.end():]
    corte = [x.end() for x in re.finditer(r"[.;\n]", resto)]
    resto = resto[:corte[1] if len(corte) > 1 else len(resto)]       # esa oracion y la siguiente
    for p in _RX_PARO_CONTEO.finditer(resto):
        tramo = resto[:p.start()]
        despues = resto[p.end():p.end() + 30]
        de_boton = re.match(r"\s*(?:con|al (?:presionar|pulsar|oprimir)|cuando (?:presione|pulse))\b", despues)
        if _RX_PARO_DE_BOTON.search(tramo[-60:]) or _RX_PARO_NEGADO.search(tramo) or de_boton:
            continue
        # De quien es el paro: de la banda (o sin sujeto), no de una luz o una pluma.
        oracion = re.split(r"[.;,\n]|\by\b", tramo)[-1] + p.group(0)
        if _RX_CTX_OTRO.search(oracion) and not _RX_CTX_BANDA.search(oracion):
            continue
        tm = None
        for x in _RX_TIEMPO.finditer(tramo):
            tm = x
        seg = 0.0
        if tm:
            seg = _numero(tm.group(1)) * (60 if tm.group(2).startswith("min") else 1)
        # Se pide un NUEVO ciclo ("el contador se reinicia", "nuevo ciclo"): tras el paro,
        # el arranque normal debe volver a mover la banda (se prueba en la simulacion).
        ciclo = bool(c.get("reinicia")) or bool(re.search(
            r"\b(?:nuevo ciclo|nueva secuencia|reinici\w*|vuelv\w* a (?:contar|empezar|arrancar)|otro ciclo)\b", t))
        return {"conteo": c["conteo"], "senal": c["senal"], "segundos": seg, "rearranque": ciclo}
    return {}


def requisitos(textos) -> list:
    """Comportamientos que se pueden probar, de la instruccion mas antigua a
    la mas reciente (la mas reciente manda: 'ya no se detenga sola' lo quita)."""
    reqs = []
    for texto in textos or []:
        # Conteo: se lee de la instruccion completa ("... al contar 3 prende la roja. Que el
        # contador se reinicie solo" son dos oraciones). La mas reciente manda.
        if _RX_SIN_CONTEO.search(_fold(texto)):
            reqs = [r for r in reqs if r["tipo"] != "conteo"]
        else:
            c = conteo_en(texto)
            if c:
                reqs = [r for r in reqs if r["tipo"] != "conteo"]
                reqs.append({"tipo": "conteo", **c, "texto": _fold(texto).strip()[:160]})
        for o in _oraciones(texto):
            # Solo oraciones sobre la BANDA: "al presionar I1 enciende la verde"
            # o "que la roja se detenga sola" hablan de luces, no del motor.
            banda = bool(_RX_CTX_BANDA.search(o))
            otro = bool(_RX_CTX_OTRO.search(re.sub(r"boton (?:verde|rojo)", "", o)))
            if not banda and otro:
                continue
            if _RX_SIN_PARO_SOLA.search(o):
                reqs = [r for r in reqs if r["tipo"] != "para_sola"]
                continue
            if not banda and not reqs:
                # sin sujeto ni requisitos previos de la banda: no se adivina
                if not _RX_PARO_SOLA.search(o):
                    continue
            if _RX_PARO_SOLA.search(o):
                # "no se detiene sola, revisa" sin condicion conserva la que ya se habia dicho
                cond = condicion_paro_solo(o) or next(
                    ({k: v for k, v in r.items() if k in ("segundos", "senal", "conteo")}
                     for r in reqs if r["tipo"] == "para_sola"), {})
                reqs = [r for r in reqs if r["tipo"] != "para_sola"]
                reqs.append({"tipo": "para_sola", **cond, "texto": o.strip()})
            for m in _RX_PARO_BOTON.finditer(o):
                s = _senal(m.group(1) or m.group(2))
                if s and not any(r["tipo"] == "para_con" and r["senal"] == s for r in reqs):
                    reqs.append({"tipo": "para_con", "senal": s, "texto": o.strip()})
            for m in _RX_ARRANQUE_BOTON.finditer(o):
                s = _senal(m.group(1) or m.group(2))
                if s:
                    reqs = [r for r in reqs if r["tipo"] != "arranca_con"]
                    reqs.append({"tipo": "arranca_con", "senal": s, "texto": o.strip()})
        # "Al llegar a 3, la banda avanza 5 s y luego se detiene": el paro es del CONTEO
        # (los 5 s se cuentan desde el objeto 3, no desde el arranque).
        pc = {} if _RX_SIN_PARO_SOLA.search(_fold(texto)) else paro_por_conteo(texto)
        if pc:
            reqs = [r for r in reqs if r["tipo"] != "para_sola"]
            reqs.append({"tipo": "para_sola", **pc, "texto": _fold(texto).strip()[:160]})
        # "la amarilla en lugar de la roja", "cambia los 10 s por 6 s": se actualiza lo ya pedido.
        subs = cambios_texto.sustituciones(texto)
        for r in reqs:
            for viejo, nuevo in subs["q"]:
                if r.get("q") == viejo:
                    r["q"] = nuevo
            for viejo, nuevo in subs["t"]:
                if r.get("segundos") is not None and abs(float(r["segundos"]) - viejo) < 0.05:
                    r["segundos"] = nuevo
        pp = pausa_por_deteccion(texto)
        if pp:
            reqs = [r for r in reqs if not (r["tipo"] == "pausa_sensor" and r["senal"] == pp["senal"])]
            reqs.append({"tipo": "pausa_sensor", **pp, "texto": _fold(texto).strip()[:160]})
        rp = repite_y_para(texto)
        if rp:
            reqs = [r for r in reqs if r["tipo"] != "para_tras_repetir"]
            reqs.append({"tipo": "para_tras_repetir", **rp, "texto": _fold(texto).strip()[:160]})
        for viejo, nuevo in [(int(a), int(b)) for a, b in re.findall(
                r"\ben vez de (\d+)(?: veces)?\s*,?\s*(?:que sean|sean) (\d+)", _fold(texto))]:
            for r in reqs:
                if r["tipo"] == "para_tras_repetir" and r["veces"] == viejo:
                    r["veces"] = nuevo
        for p in parpadeo_en(texto):
            # "ahora la amarilla en lugar de la roja": la mas reciente manda (una por disparador).
            reqs = [r for r in reqs if not (r["tipo"] == "parpadea" and r.get("senal") == p["senal"])]
            reqs.append({"tipo": "parpadea", **p, "texto": _fold(texto).strip()[:160]})
        tg = toggle_en(texto)
        if tg:
            reqs = [r for r in reqs if r["tipo"] != "toggle"]
            reqs.append({"tipo": "toggle", **tg, "texto": _fold(texto).strip()[:160]})
        # Cambios sobre lo ya pedido: la instruccion mas reciente manda.
        n = None if conteo_en(texto) else cambio_conteo(texto)
        if n:
            for r in reqs:
                if r.get("conteo"):
                    r["conteo"] = n
        if quita_paro(texto):
            # "Al llegar a 4 ya no se detiene": se quita el paro y, si era del conteo,
            # se exige lo contrario (que siga avanzando al contar).
            viejos = [r for r in reqs if r["tipo"] == "para_sola"]
            reqs = [r for r in reqs if r["tipo"] not in ("para_sola", "sigue_al_contar")]
            cont = next((r for r in viejos if r.get("conteo")), None) or \
                next((r for r in reqs if r["tipo"] == "conteo"), None)
            if cont:
                reqs.append({"tipo": "sigue_al_contar", "conteo": cont["conteo"], "senal": cont["senal"],
                             "texto": _fold(texto).strip()[:160]})
    # Arranque por defecto: si nadie dijo con que arranca, I1.
    todo = " ".join(_fold(t) for t in textos or [])
    if (not any(r["tipo"] in ("arranca_con", "toggle") for r in reqs) and _RX_CTX_BANDA.search(todo)
            and _RX_AVANZA.search(todo) and not _RX_ARRANQUE_OTRO.search(todo)):
        reqs.append({"tipo": "arranca_con", "senal": "I1", "defecto": True,
                     "texto": "no se dijo con que arranca: con I1"})
    # Pausa en cada deteccion dentro de un ciclo que se repite N veces: solo las N-1
    # primeras vueltas deben reanudar (tras la ultima se detiene).
    rep = next((r for r in reqs if r["tipo"] == "para_tras_repetir"), None)
    for r in reqs:
        if r["tipo"] == "pausa_sensor" and rep and rep["senal"] == r["senal"]:
            r["ciclos"] = max(1, min(2, int(rep["veces"]) - 1))
    # Pausa y paro por conteo en el MISMO sensor: la prueba del paro espera cada pausa; la
    # prueba suelta del conteo no aplica (veria la banda detenida en cada caja) y el
    # reinicio lo prueba el nuevo ciclo del paro.
    pausa = next((r for r in reqs if r["tipo"] == "pausa_sensor"), None)
    if pausa:
        for r in reqs:
            if r["tipo"] == "para_sola" and r.get("conteo") and r.get("senal") == pausa["senal"]:
                r["pausa_s"] = pausa["segundos"]
                reqs = [x for x in reqs if not (x["tipo"] == "conteo" and x.get("senal") == pausa["senal"])]
                break
    # "al contar 3 cajas se detenga sola" ya lo prueba para_sola (con su conteo), salvo
    # que el contador ademas se reinicie (eso lo prueba el requisito "conteo").
    if any(r["tipo"] == "para_sola" and r.get("conteo") for r in reqs):
        reqs = [r for r in reqs if r["tipo"] != "conteo" or r.get("reinicia") or r.get("boton")]
    return reqs


def toggle_en(texto: str) -> dict:
    """{"senal": I1|I2} si el texto pide que el MISMO boton arranque y detenga la
    banda (alternancia). Sin boton reconocible no se adivina."""
    t = _fold(texto)
    m = _RX_TOGGLE.search(t)
    if not m or not _RX_CTX_BANDA.search(t) or not _RX_TOGGLE_PARO.search(t):
        return {}
    a = _RX_ARRANQUE_BOTON.search(t)
    b = _senal(next(g for g in a.groups() if g)) if a else None
    if b not in ("I1", "I2"):
        botones = [_senal(x.group(1)) for x in _RX_BOTON_TEXTO.finditer(t[:m.end()])]
        b = botones[-1] if botones else None
    return {"senal": b} if b in ("I1", "I2") else {}


def de_aclaracion(acl: dict) -> dict:
    """Respuesta a la pregunta 'paro_solo' -> condicion."""
    v = (acl or {}).get("paro_solo") or {}
    return condicion_paro_solo(v.get("respuesta") or "") if v else {}


def de_sesion(historial, texto: str, acl: dict) -> list:
    """Requisitos de toda la sesion + la respuesta a 'paro_solo' si la hay."""
    reqs = requisitos(list(historial or []) + [texto])
    falla = _fold(((acl or {}).get("falla") or {}).get("respuesta"))
    if "sola" in falla and not any(r["tipo"] == "para_sola" for r in reqs):
        reqs.append({"tipo": "para_sola", "texto": "la banda no se detiene sola"})
    cond = de_aclaracion(acl)
    if cond:
        reqs = [r for r in reqs if r["tipo"] != "para_sola"]
        reqs.append({"tipo": "para_sola", **cond, "texto": acl["paro_solo"]["respuesta"]})
    return reqs


def paro_solo_sin_condicion(reqs: list, acl: dict) -> bool:
    """Se pide que la banda se detenga sola, pero nadie dijo cuando."""
    if "paro_solo" in (acl or {}):
        return False
    return any(r["tipo"] == "para_sola" and not any(k in r for k in ("segundos", "senal", "conteo"))
               for r in reqs)


def pregunta_paro_solo() -> dict:
    return {"id": "paro_solo", "campo": "paro_solo",
            "pregunta": "¿Cuándo debe detenerse la banda sola (sin presionar ningún botón)?",
            "opciones": ["A los 10 s de marcha", "A los 30 s de marcha", "Cuando S1 detecte",
                         "Cuando S2 detecte", "Al contar 3 cajas en S2"]}


def bloque_modelo(reqs: list) -> str:
    """Lo que el modelo debe cumplir (y que despues se prueba simulando)."""
    filas = []
    for r in reqs:
        if r["tipo"] == "para_sola":
            if r.get("conteo"):
                c = (f"al contar {r['conteo']} cajas en {r['senal']}: contador CTn de {r['senal']} (modo una_vez, "
                     f"preset {r['conteo']}, reset = la senal de arranque) y CTn en el paro del motor")
            elif r.get("senal"):
                c = f"cuando {r['senal']} detecte: {r['senal']} en el paro del motor"
            elif r.get("segundos"):
                c = (f"a los {_fmt(r['segundos'])} s de marcha: timer TON con entrada BANDA_CORRIENDO y "
                     f"{_fmt(r['segundos'])} s, y ese timer en el paro del motor")
            else:
                c = "sin que nadie presione un boton (timer TON de BANDA_CORRIENDO en el paro del motor)"
            filas.append(f"- La banda se DETIENE SOLA {c}. Conserva el paro manual que ya hubiera: si motor.paro "
                         f"ya tenia una senal (p. ej. I2), usa una condicion OR con las dos. Si la banda la mueve "
                         f"una secuencia, el paro automatico va en el \"paro\" de la secuencia.")
        elif r["tipo"] == "conteo":
            n, s, b = r["conteo"], r["senal"], r.get("boton")
            accion = (f"la accion disparada por ese CTn: con duracion = timer PULSO de entrada CTn con esos "
                      f"segundos (o evento \"cuando\": CTn con duracion_s); detener la banda = CTn en el paro")
            parar = (f" {b} tambien es el paro de la banda: al presionarlo se detiene Y se borra la cuenta."
                     if b == "I2" else "")
            if r.get("reinicia") and b:
                filas.append(
                    f"- Al contar {n} en {s} pasa lo pedido; el contador VUELVE A CERO SOLO y TAMBIEN al presionar "
                    f"{b} (borra la cuenta a medias). Receta probada en el PLC: contador {{\"entrada\":\"{s}\","
                    f"\"preset\":{n},\"modo\":\"ciclico\",\"reset\":\"{b}\"}} (pulso al llegar a {n}, vuelve a 0 "
                    f"solo y {b} lo borra) y {accion}.{parar} NO uses SQ1_FIN si no hay secuencia.")
            elif r.get("reinicia"):
                filas.append(
                    f"- Al contar {n} en {s} pasa lo pedido y el contador VUELVE A CERO SOLO (la siguiente vez "
                    f"tambien debe pasar al llegar otra vez a {n}, no antes). Receta probada en el PLC: contador "
                    f"{{\"entrada\":\"{s}\",\"preset\":{n},\"modo\":\"ciclico\"}} (pulso al llegar a {n} y vuelve "
                    f"a 0 solo) y {accion}. Otra forma valida: modo una_vez con reset = el timer de la accion. NO "
                    f"uses reset SQ1_FIN si no hay secuencia, ni un boton salvo que el usuario lo pida.")
            elif b:
                filas.append(
                    f"- Al contar {n} en {s} pasa lo pedido y el contador se queda en {n} hasta que se presiona "
                    f"{b}, que lo vuelve a cero (tambien borra una cuenta a medias). Contador {{\"entrada\":\"{s}\","
                    f"\"preset\":{n},\"modo\":\"una_vez\",\"reset\":\"{b}\"}} y {accion}.{parar} El reset es {b} "
                    f"(lo pidio el usuario): no lo cambies por la senal de arranque ni por SQ1_FIN.")
            else:
                filas.append(
                    f"- Al contar {n} en {s} pasa lo pedido. Contador {{\"entrada\":\"{s}\",\"preset\":{n},"
                    f"\"modo\":\"una_vez\"}} con reset = la senal de arranque (al arrancar de nuevo cuenta desde "
                    f"cero); NO uses reset SQ1_FIN si no hay secuencia.")
            if r.get("boton_invalido"):
                filas.append(
                    f"- El usuario pidio reiniciar el contador con {r['boton_invalido']}, pero {r['boton_invalido']} "
                    f"es el PARO DE EMERGENCIA y no puede reiniciar nada: usa I1 o I2 y dilo en "
                    f"analisis.modificado.")
        elif r["tipo"] == "toggle":
            b = r["senal"]
            filas.append(
                f"- ALTERNANCIA con {b}: el 1er pulso de {b} arranca la banda, el 2o la detiene, el 3o la arranca... "
                f"Receta PROBADA en el PLC: una secuencia libre (SQ1, o SQ2 si SQ1 ya se usa) {{\"inicio\":\"{b}\","
                f"\"modo\":\"una_vez\",\"pasos\":[{{\"motor\":[\"marcha\"],\"avanza\":\"flanco\",\"senal\":\"{b}\"}}]}}: "
                f"el pulso que la inicia pone la banda en marcha y el siguiente flanco de {b} termina la secuencia "
                f"(la banda se detiene). Puede llevar \"paro\" (I2) si el usuario lo pide. NO uses motor.arranque/"
                f"motor.paro con {b} ni una condicion '{b} AND BANDA_CORRIENDO' ni memorias set/reset con {b}: "
                f"simulado en el ST, la banda nunca arranca. Los Hz van en banda.frecuencia_hz.")
        elif r["tipo"] == "para_con":
            filas.append(f"- {r['senal']} detiene la banda.")
        elif r["tipo"] == "arranca_con":
            filas.append(f"- {r['senal']} arranca la banda.")
    if not filas:
        return ""
    return ("COMPORTAMIENTO QUE SE VA A PROBAR SIMULANDO EL PLC (tiene que cumplirse):\n" + "\n".join(filas))


def _fmt(x) -> str:
    return str(int(x)) if float(x).is_integer() else str(x)


# ── Prueba en el simulador ─────────────────────────────────────────────────
def _entradas_arranque(band) -> list:
    """Entradas a pulsar para arrancar la banda ([] = arranca sola al cargar)."""
    try:
        cfg = lm.compilar_motor(band)
    except ValueError:
        return ["I1"]
    if not cfg[lm.H_MOVE]:
        return []
    eng = lm.normalizar_motor((band or {}).get("engine") or {})
    origen = lm.nombre_senal(cfg[lm.M_RUN])
    if not origen:
        seq = next((s for s in lm._lista(eng.get("secuencias")) if isinstance(s, dict)
                    and any("marcha" in (p.get("motor") or []) for p in lm._lista(s.get("pasos"))
                            if isinstance(p, dict))), {})
        origen = seq.get("inicio")
    op, entradas, _auto = lm._fuentes_arranque(origen, eng, set())
    botones = [x for x in entradas if x in ("I1", "I2", "I4", "I5")]
    if op == "y":
        return botones
    return botones[:1]


def _sim(band):
    import lavo_sim
    s = lavo_sim.SimV7()
    err, campo = s.cargar({"band": band})
    if err:
        raise ValueError(f"el PLC simulado rechazo la configuracion (error {err}, campo {campo})")
    return s


def _arrancar(s, entradas):
    if not entradas:
        s.correr(0.5)
        return
    for e in entradas:
        s.poner(**{e: True})
    s.correr(0.3)
    for e in entradas:
        s.poner(**{e: False})


def _corre(s) -> bool:
    return s.motor() in ("dir1", "dir2")


def verificar(cfg: dict, reqs: list) -> list:
    """Problemas de comportamiento (lista vacia = cumple lo que se pudo probar)."""
    if not reqs:
        return []
    band = (cfg or {}).get("band") or {}
    if not ((band.get("engine") or {}).get("version") == 7 or lm.es_motor_v7(cfg or {})):
        return []
    problemas = []
    arranque = _entradas_arranque(band)
    pedido = next((r for r in reqs if r["tipo"] == "arranca_con"), None)
    if pedido:
        arranque = [pedido["senal"]]
    import time
    limite = time.monotonic() + PRESUPUESTO_S
    try:
        for r in reqs:
            if time.monotonic() > limite:
                break                     # sin tiempo: lo que falta no se reporta como falla
            # Cada requisito se simula aparte: el resultado se reutiliza (cache)
            # cuando el mismo programa vuelve a probarse con el mismo requisito.
            prueba = {"r": r, "arranque": arranque}
            fallas = cache_simulacion.obtener("comportamiento", band, prueba)
            if fallas is None:
                fallas = _verificar_req(band, r, arranque)
                cache_simulacion.guardar("comportamiento", band, prueba, fallas)
            problemas += fallas
    except ValueError as e:
        problemas.append(str(e))
    return problemas


def _verificar_req(band: dict, r: dict, arranque: list) -> list:
    """Fallas de UN requisito en una simulacion nueva (lo que verificar hacia por cada r)."""
    problemas = []
    s = _sim(band)
    if r["tipo"] == "toggle":
        problemas += _probar_toggle(s, r)
        return problemas
    if r["tipo"] == "conteo":
        # No exige que la banda corra (un conteo puede manejar solo luces o plumas).
        s._band_conteo = band
        _arrancar(s, arranque)
        s.correr(_ARRANQUE_S)
        problemas += _probar_conteo(s, r, arranque)
        return problemas
    if r["tipo"] == "arranca_con":
        _arrancar(s, [r["senal"]])
        s.correr(_ARRANQUE_S)
        if not _corre(s):
            problemas.append(f"Al presionar {r['senal']} la banda no arranca.")
        return problemas
    _arrancar(s, arranque)
    s.correr(_ARRANQUE_S)
    if not _corre(s):
        problemas.append("La banda no arranca" + (f" con {', '.join(arranque)}" if arranque else "")
                         + f", asi que no se puede comprobar: «{r['texto']}».")
        return problemas
    if r["tipo"] == "para_con":
        s.pulsar(r["senal"])
        s.correr(_FRENO_S)
        if _corre(s):
            problemas.append(f"Al presionar {r['senal']} la banda NO se detiene.")
    elif r["tipo"] == "para_sola":
        problemas += _probar_para_sola(s, r, band, arranque)
    elif r["tipo"] == "para_tras_repetir":
        problemas += _probar_repite_y_para(s, r)
    elif r["tipo"] == "pausa_sensor":
        problemas += _probar_pausa(s, r)
    elif r["tipo"] == "parpadea":
        problemas += _probar_parpadeo(s, r)
    elif r["tipo"] == "sigue_al_contar":
        n, senal = int(r["conteo"]), r["senal"]
        sensor = "I4" if senal == "S1" else "I5"
        for _ in range(n):
            s.pulsar(sensor, 0.3)
            s.correr(0.7)
        s.correr(4.0)
        if not _corre(s):
            problemas.append(f"Al contar {n} en {senal} la banda se detiene, y se pidió que ya NO se detenga "
                             f"(quita el paro del motor por el contador y deja solo las otras acciones).")
    return problemas


_ESPERA_FIN_ACCION_S = 20     # lo maximo que se espera a que termine la accion del conteo
_ESPERA_ACCION_S = 12         # lo maximo que se espera a que EMPIECE la accion tras el objeto N


def _estado(s) -> set:
    """Lo observable: salidas encendidas y si la banda corre."""
    return set(s.salidas()) | {"BANDA_CORRIENDO" if _corre(s) else "BANDA_DETENIDA"}


def _ronda(s, sensor, n) -> list:
    """Pasa n objetos por el sensor; el estado despues de cada uno."""
    estados = []
    for _ in range(n):
        s.poner(**{sensor: True})
        s.correr(0.3)
        s.poner(**{sensor: False})
        s.correr(0.4)
        estados.append(_estado(s))
    return estados


def _probar_toggle(s, r) -> list:
    """4 pulsos del mismo boton: corre, parada, corre, parada."""
    b = r["senal"]
    s.correr(0.5)
    visto = []
    for _ in range(4):
        s.pulsar(b, 0.4)
        s.correr(_ARRANQUE_S)
        visto.append("en marcha" if _corre(s) else "detenida")
    if visto == ["en marcha", "detenida", "en marcha", "detenida"]:
        return []
    return [f"Con pulsos de {b} la banda debe alternar (en marcha, detenida, en marcha, detenida) y se observo: "
            f"{', '.join(visto)}. Usa la receta de ALTERNANCIA: secuencia una_vez con inicio {b} y UN paso "
            f"{{\"motor\":[\"marcha\"],\"avanza\":\"flanco\",\"senal\":\"{b}\"}}, sin motor.arranque/paro con {b}."]


def _contador_atorado(s, senal) -> bool:
    """Algun contador de ese sensor sigue activo (salida del contador en LSIG[88 + k])."""
    band = getattr(s, "_band_conteo", None) or {}
    eng = lm.normalizar_motor((band or {}).get("engine") or {})
    for c in lm._lista(eng.get("contadores")):
        if not isinstance(c, dict):
            continue
        try:
            k = int(str(c.get("id", "")).upper().replace("CT", "")) - 1
            if lm.codigo_senal(c.get("entrada")) == lm.codigo_senal(senal) and 0 <= k < 16 and s.var("LSIG", 88 + k):
                return True
        except (ValueError, TypeError):
            continue
    return False


def _probar_conteo(s, r, arranque) -> list:
    """Ronda 1: al objeto N cambia algo que no habia pasado con los anteriores.
    Ronda 2 (si se reinicia solo): cuando termina esa accion se vuelve a contar
    y ese cambio aparece OTRA VEZ al objeto N, no antes."""
    n, sensor = int(r["conteo"]), ("I4" if r["senal"] == "S1" else "I5")
    antes = [_estado(s)] + _ronda(s, sensor, n - 1)
    al_n = _ronda(s, sensor, 1)[0]
    vistos = set().union(*antes)

    def _cambio(estado):
        c = estado - vistos
        if "BANDA_DETENIDA" in c and "BANDA_CORRIENDO" not in vistos:
            c.discard("BANDA_DETENIDA")           # nunca corrio: no es un efecto del conteo
        return c

    cambio = _cambio(al_n)
    # La accion puede llegar unos segundos despues del objeto N ("al llegar a 3, la banda
    # avanza 5 s y luego se detiene"): se espera sin tocar nada.
    t = 0.0
    while not cambio and t < _ESPERA_ACCION_S:
        s.correr(0.5)
        t += 0.5
        cambio = _cambio(_estado(s))
    if cambio and t > 0:
        # Llego tarde: solo cuenta si NO pasa igual sin el objeto N (si no, es por tiempo).
        band = getattr(s, "_band_conteo", None)
        if band is not None:
            c = _replica(band, arranque, sensor, n - 1, 0.7 + t, objetos_s=0.7, arranque_s=_ARRANQUE_S)
            if cambio & _cambio(_estado(c)):
                cambio = set()
    if not cambio:
        return [f"Al contar {n} en {r['senal']} no pasa nada (ninguna salida ni la banda cambian)."]
    if r.get("reinicia"):
        problemas = _probar_reinicio_solo(s, r, arranque, cambio, n, sensor)
        if problemas:
            return problemas
    if r.get("boton"):
        return _probar_reset_boton(s, r, arranque, cambio, n, sensor)
    return []


def _aparece(cambio: set, estado: set) -> bool:
    """La accion del conteo esta presente en ese estado."""
    return bool(cambio & estado - {"BANDA_DETENIDA"}) or ("BANDA_DETENIDA" in cambio and "BANDA_DETENIDA" in estado)


def _esperar_fin(s, cambio) -> bool:
    """Espera a que la accion termine (sin contar el paro de la banda). True si termino."""
    t = 0.0
    while cambio & _estado(s) - {"BANDA_DETENIDA"} and t < _ESPERA_FIN_ACCION_S:
        s.correr(0.5)
        t += 0.5
    return not cambio & _estado(s) - {"BANDA_DETENIDA"}


def _rearrancar(s, arranque):
    if not _corre(s) and arranque:
        _arrancar(s, arranque)
        s.correr(_ARRANQUE_S)


def _probar_reinicio_solo(s, r, arranque, cambio, n, sensor) -> list:
    """Ronda 2: cuando termina la accion se vuelve a contar y la accion sale OTRA VEZ al objeto N, no antes."""
    if not _esperar_fin(s, cambio):
        # La accion no termina sola ("prende la roja" sin tiempo): no hay 2a vuelta que probar,
        # pero si el contador sigue ACTIVO (CTn en LSIG[88 + n-1]) es que nunca se reinicio.
        if _contador_atorado(s, r["senal"]):
            return [f"El contador no vuelve a cero solo: despues de llegar a {n} se queda activo y lo que "
                    f"enciende no se apaga (para reiniciarse solo usa modo ciclico, y la accion con su duracion "
                    f"como timer PULSO del contador)."]
        return []
    if "BANDA_DETENIDA" in cambio:
        _rearrancar(s, arranque)
    ronda2 = _ronda(s, sensor, n)
    temprano = next((k + 1 for k, e in enumerate(ronda2[:-1]) if _aparece(cambio, e)), None)
    if temprano:
        return [f"El contador no vuelve a cero solo: en la segunda vuelta la accion de llegar a {n} ya sale con "
                f"el objeto {temprano} (el reset del contador no se activa; sin secuencia no uses SQ1_FIN)."]
    if not _aparece(cambio, ronda2[-1]):
        return [f"El contador no vuelve a cero solo: la segunda vez que llegan {n} objetos a {r['senal']} ya no "
                f"pasa nada (el contador se quedo arriba; para reiniciarse solo usa modo ciclico o reset = el "
                f"timer de la accion)."]
    return []


def _probar_reset_boton(s, r, arranque, cambio, n, sensor) -> list:
    """El boton vuelve a cero el contador: tras presionarlo, contar de nuevo hasta N
    hace la accion; y presionarlo a media cuenta la borra (N-1, boton, N-1: nada)."""
    b = r["boton"]
    # El contador cuenta aunque la banda este detenida: solo se vuelve a arrancar si la accion
    # del conteo ES detener la banda (si no, el boton de arranque podria reiniciarlo y ocultar
    # que el reset quedo en otro boton).
    rearrancar = (lambda: _rearrancar(s, arranque)) if "BANDA_DETENIDA" in cambio else (lambda: None)
    s.pulsar(b)
    s.correr(0.5)
    if not _esperar_fin(s, cambio) and _contador_atorado(s, r["senal"]):
        return [f"Al presionar {b} el contador NO vuelve a cero (sigue activo despues de llegar a {n}): usa "
                f"reset = {b} en el contador."]
    rearrancar()
    if n > 1:
        r1 = _ronda(s, sensor, n - 1)
        s.pulsar(b)
        s.correr(0.5)
        rearrancar()
        r2 = _ronda(s, sensor, n - 1)
        k = next((i + 1 for i, e in enumerate(r1 + r2) if _aparece(cambio, e)), None)
        if k:
            return [f"Presionar {b} no borra la cuenta: con {n - 1} objetos, {b} y otros {n - 1}, la accion de "
                    f"llegar a {n} ya salio (objeto {k}). Usa reset = {b} en el contador."]
    if not _aparece(cambio, _ronda(s, sensor, 1)[0]):
        return [f"Despues de reiniciar el contador con {b}, al llegar otra vez a {n} objetos ya no pasa nada."]
    return []


_QUIETA_S = 6.0          # tras el paro automatico, sin tocar nada, debe seguir detenida


def _replica(band, arranque, sensor, objetos, extra_s, objetos_s=1.0, arranque_s=_ARRANQUE_S):
    """Simulacion de control: misma linea de tiempo (arranque, 'objetos' objetos cada
    'objetos_s' s y 'extra_s' s mas) pero sin el ultimo objeto."""
    c = _sim(band)
    _arrancar(c, arranque)
    c.correr(arranque_s)
    for _ in range(objetos):
        c.pulsar(sensor, 0.3)
        c.correr(objetos_s - 0.3)
    c.correr(extra_s)
    return c


def _probar_para_sola(s, r, band=None, arranque=None) -> list:
    if r.get("conteo"):
        return _probar_paro_conteo(s, r, band, arranque)
    if r.get("senal"):
        s.pulsar(r["senal"], 0.3)
        s.correr(_FRENO_S)
        return [] if not _corre(s) else [f"Cuando {r['senal']} detecta, la banda NO se detiene sola."]
    if r.get("segundos"):
        n = float(r["segundos"])
        antes = max(0.0, n - 1.0 - _ARRANQUE_S)
        if antes:
            s.correr(antes)
            if not _corre(s):
                return [f"La banda se detiene antes de los {_fmt(n)} s pedidos."]
        s.correr(max(0.0, n - _ARRANQUE_S - antes) + _FRENO_S + 0.5)
        return [] if not _corre(s) else [f"La banda NO se detiene sola a los {_fmt(n)} s de marcha."]
    for _ in range(int(MAX_SOLA_S / 5)):
        s.correr(5)
        if not _corre(s):
            return []
    return [f"La banda NO se detiene sola: despues de {MAX_SOLA_S} s sin tocar ningun boton sigue en marcha "
            f"(su paro solo depende de botones)."]


def _probar_paro_conteo(s, r, band=None, arranque=None) -> list:
    """Paro ligado al conteo: corre con los N-1 primeros objetos; desde el objeto N
    sigue en marcha los 'segundos' pedidos (si los hay), luego se detiene y,
    sin tocar nada, ya no vuelve a arrancar. Los segundos se cuentan desde el
    objeto N, no desde el arranque."""
    n, senal = int(r["conteo"]), r["senal"]
    sensor = "I4" if senal == "S1" else "I5"
    seg = float(r.get("segundos") or 0)
    pausa = float(r.get("pausa_s") or 0)       # el mismo sensor tambien pausa la banda en cada deteccion
    for k in range(n):
        if not _corre(s):
            return [f"La banda se detiene antes de contar {n} cajas en {senal} (se detuvo en la caja {k})."]
        s.pulsar(sensor, 0.3)
        if k < n - 1:
            s.correr(0.7)
            t = 0.0
            while pausa and not _corre(s) and t < pausa + 4:     # su pausa: espera a que reanude
                s.correr(0.5)
                t += 0.5
    if pausa:
        # Con pausa en el mismo sensor el momento exacto depende del diseño (pausa y luego
        # el avance, o el avance directo): se exige que avance despues de la caja N y que
        # termine detenida.
        avanzo, t = False, 0.0
        while t < pausa + seg + 4:
            s.correr(0.5)
            t += 0.5
            avanzo = avanzo or _corre(s)
        if seg and not avanzo:
            return [f"Al contar {n} en {senal} la banda ya no avanza los {_fmt(seg)} s pedidos."]
        if _corre(s):
            return [f"Al contar {n} en {senal} la banda NO se detiene sola despues de avanzar {_fmt(seg)} s."]
        band = None                           # el control por tiempo no aplica con pausas
    # Desde el objeto N (los timers del PLC van por pulsos de 1 s: +-1 s de margen).
    elif seg:
        sigue = max(0.0, seg - 1.3)
        if sigue:
            s.correr(sigue)
            if not _corre(s):
                return [f"Al contar {n} en {senal} la banda se detiene enseguida: debia seguir avanzando "
                        f"{_fmt(seg)} s y luego detenerse."]
        s.correr(seg - sigue + _FRENO_S)
        if _corre(s):
            return [f"Al contar {n} en {senal} la banda NO se detiene sola despues de avanzar {_fmt(seg)} s "
                    f"(los {_fmt(seg)} s se cuentan desde la caja {n}, no desde el arranque)."]
    else:
        s.correr(_FRENO_S)
        if _corre(s):
            return [f"La banda NO se detiene sola al contar {n} cajas en {senal}."]
    # Control: lo mismo SIN la caja N. Si tambien se detiene, el paro es por tiempo, no por el conteo.
    if band is not None:
        c = _replica(band, arranque, sensor, n - 1, 0.3 + seg + _FRENO_S)
        if not _corre(c):
            return [f"La banda se detiene por tiempo, no por el conteo: sin la caja {n} tambien se detiene "
                    f"(debe esperar a contar {n} en {senal}" + (f" y luego avanzar {_fmt(seg)} s)." if seg else ").")]
    s.correr(_QUIETA_S)
    if _corre(s):
        return [f"Despues de detenerse al contar {n} en {senal}, la banda vuelve a arrancar sola sin tocar nada."]
    # Nuevo ciclo: terminadas las acciones (salidas quietas), el arranque normal la vuelve a mover.
    if arranque and r.get("rearranque"):
        t = 0.0
        while s.salidas() and t < 15:
            s.correr(0.5)
            t += 0.5
        _arrancar(s, arranque)
        s.correr(_ARRANQUE_S)
        if not _corre(s):
            return [f"Despues del paro automatico al contar {n} en {senal}, al presionar {', '.join(arranque)} la "
                    f"banda ya no vuelve a arrancar (el paro del motor se queda activo: usa una senal que se "
                    f"libere, p. ej. el pulso SQ1_CICLO o el timer de la accion, no una condicion que siga activa)."]
    return []


_NOMBRE_LUZ = {"Q3": "verde", "Q4": "amarilla", "Q5": "roja"}


def _probar_parpadeo(s, r) -> list:
    """Tras su disparador, la luz debe encenderse y apagarse al menos 2 veces en 8 s."""
    q = r["q"]
    if r.get("senal"):
        s.pulsar("I4" if r["senal"] == "S1" else "I5" if r["senal"] == "S2" else r["senal"], 0.3)
    prev, flancos, encendida = False, 0, False
    for _ in range(80):
        s.correr(0.1)
        v = q in s.salidas()
        encendida = encendida or v
        if v and not prev:
            flancos += 1
        prev = v
    if flancos >= 2:
        return []
    luz = _NOMBRE_LUZ.get(q, q)
    cuando = f" al detectar {r['senal']}" if r.get("senal") else ""
    estado = "se queda fija" if encendida else "no se enciende"
    return [f"La luz {luz} debe PARPADEAR{cuando} y {estado}. Para parpadear usa un timer INTERMITENTE (tipo 6, "
            f"segundos encendido / segundos_off apagado) cuya entrada sea lo que la activa (p. ej. el paso o "
            f"SQn_ACTIVA de la secuencia, o el timer que dura lo pedido) y en 'salidas' {q} = ese timer; no pongas "
            f"{q} fija en los pasos de una secuencia exclusiva."]


def _probar_repite_y_para(s, r) -> list:
    """N vueltas (cada una la dispara un objeto en el sensor): entre vuelta y vuelta la
    banda vuelve a avanzar; tras la N-esima se queda detenida (6 s sin moverse)."""
    n, sensor = int(r["veces"]), ("I4" if r["senal"] == "S1" else "I5")
    for k in range(1, n + 1):
        s.pulsar(sensor, 0.3)
        if k < n:
            t = 0.0
            while not _corre(s) or t < 1.0:
                s.correr(0.5)
                t += 0.5
                if t > 20:
                    return [f"Tras la vuelta {k} de {n} la banda ya no vuelve a avanzar."]
            s.correr(1.0)
    t = 0.0
    while t < 20:
        s.correr(0.5)
        t += 0.5
        if not _corre(s):
            quieta = 0.0
            while quieta < 6 and not _corre(s):
                s.correr(0.5)
                quieta += 0.5
            if quieta >= 6:
                return []
    return [f"Despues de repetir {n} veces la banda NO se queda detenida (sigue avanzando o vuelve a arrancar): "
            f"al terminar la ultima vuelta detén el motor (p. ej. paro con SQn_FIN o SQn_CICLO de la secuencia que "
            f"cuenta las vueltas) y no dejes un arranque enclavado que la vuelva a mover."]
