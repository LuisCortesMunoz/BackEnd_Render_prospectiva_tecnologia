// Generado : openai/gpt-oss-120b | 2026-06-04 17:49:53
// Consulta : Crea un renglon de enclavamiento con esta estructura exacta: fila 0 en serie: primero XIO %I2 (boton paro NC), luego XIC %I1 (boton arranque NA), luego la bobina OTE %M1 como salida. fila 1 paralela UNICAMENTE con XIC %M1 en paralelo con XIC %I1, para que M1 se auto-retenga al soltar I1. I2 NO va en la rama paralela, solo en la fila 0 para cortar el circuito al parar.
// Rungs: 1 | Ramas paralelas: 1 | Variables: 3

export const program = {
  "metadata": {
    "project_id": "import_20260604174953",
    "name": "Enclavamiento_Boton",
    "version": "1.0.0",
    "plc_target": {
      "ip": "192.168.1.100",
      "port": 502,
      "unit_id": 1
    },
    "scan_time_ms": 100,
    "_explicacion": "Cuando el botón de arranque %I1 se pulsa y el paro %I2 está libre, la bobina %M1 se energiza y se mantiene activada por su propio contacto en la rama paralela; al liberar %I1 la salida sigue activa hasta que %I2 se cierra.",
    "_implementacion": "Insertar los contactos y la bobina en el primer renglón según el orden indicado -> Añadir una rama paralela con el contacto XIC %M1 para la auto‑retención",
    "_python_modbus": null
  },
  "symbol_table": {
    "I0.1": {
      "symbol": "I0_1",
      "type": "BOOL",
      "modbus": {
        "fn": "read_coil",
        "address": null
      },
      "comment": "Entrada — %I1"
    },
    "I0.2": {
      "symbol": "I0_2",
      "type": "BOOL",
      "modbus": {
        "fn": "read_coil",
        "address": null
      },
      "comment": "Entrada — %I2"
    },
    "M0.1": {
      "symbol": "M0_1",
      "type": "BOOL",
      "modbus": {
        "fn": "internal",
        "address": null
      },
      "comment": "Marca — %M1"
    }
  },
  "rungs": [
    {
      "id": 1,
      "enabled": true,
      "comment": "Renglón de enclavamiento arranque/parada",
      "network": [
        {
          "row": 0,
          "elements": [
            {
              "id": "e20260604174953r0f0c0",
              "type": "contact_nc",
              "address": "I0.2",
              "pos": {
                "col": 0
              }
            },
            {
              "id": "e20260604174953r0f0c1",
              "type": "contact_no",
              "address": "I0.1",
              "pos": {
                "col": 1
              }
            },
            {
              "id": "e20260604174953r0f0c2",
              "type": "coil",
              "address": "M0.1",
              "pos": {
                "col": 2
              },
              "coil_type": "output"
            }
          ]
        },
        {
          "row": 1,
          "elements": [
            {
              "id": "e20260604174953r0f1c0",
              "type": "contact_no",
              "address": "M0.1",
              "pos": {
                "col": 0
              }
            }
          ]
        }
      ]
    }
  ],
  "execution_state": {
    "mode": "run",
    "rung_states": {},
    "forced_outputs": {}
  }
};

export default program;
