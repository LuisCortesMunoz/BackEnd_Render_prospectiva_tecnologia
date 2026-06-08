// Generado : openai/gpt-oss-120b | 2026-06-04 12:15:06
// Consulta : Crea un renglon de enclavamiento con esta estructura exacta: fila 0 en serie: primero XIO %I2 (boton paro NC), luego XIC %I1 (boton arranque NA), luego la bobina OTE %M1 como salida. fila 1 paralela UNICAMENTE con XIC %M1 en paralelo con XIC %I1, para que M1 se auto-retenga al soltar I1. I2 NO va en la rama paralela, solo en la fila 0 para cortar el circuito al parar.
// Rungs: 1 | Ramas paralelas: 1 | Variables: 4

export const program = {
  "metadata": {
    "project_id": "import_20260604121506",
    "name": "Enclavamiento_M1",
    "version": "1.0.0",
    "plc_target": {
      "ip": "192.168.1.100",
      "port": 502,
      "unit_id": 1
    },
    "scan_time_ms": 100,
    "_explicacion": "El contacto NC de paro y el de emergencia impiden la activación; al pulsar arranque se cierra M1 que se mantiene mediante su propio contacto en la rama paralela.",
    "_implementacion": "Insertar los contactos y bobina en la red 1 del programa ladder → Añadir la rama paralela con el contacto XIC %M1 para auto‑retención",
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
    "I0.8": {
      "symbol": "I0_8",
      "type": "BOOL",
      "modbus": {
        "fn": "read_coil",
        "address": null
      },
      "comment": "Entrada — %I8"
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
      "comment": "Renglon de enclavamiento con retención de marca M1",
      "network": [
        {
          "row": 0,
          "elements": [
            {
              "id": "e20260604121506r0f0c0",
              "type": "contact_nc",
              "address": "I0.2",
              "pos": {
                "col": 0
              }
            },
            {
              "id": "e20260604121506r0f0c1",
              "type": "contact_no",
              "address": "I0.1",
              "pos": {
                "col": 1
              }
            },
            {
              "id": "e20260604121506r0f0c2",
              "type": "contact_nc",
              "address": "I0.8",
              "pos": {
                "col": 2
              }
            },
            {
              "id": "e20260604121506r0f0c3",
              "type": "coil",
              "address": "M0.1",
              "pos": {
                "col": 3
              },
              "coil_type": "output"
            }
          ]
        },
        {
          "row": 1,
          "elements": [
            {
              "id": "e20260604121506r0f1c0",
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
