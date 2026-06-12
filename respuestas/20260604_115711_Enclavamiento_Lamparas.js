// Generado : openai/gpt-oss-120b | 2026-06-04 11:57:11
// Consulta : Quiero un enclavamiento: encender la lampara verde Q10 con el boton de arranque I1 y que se mantenga encendida al soltar el boton. Apagarla con el paro I2.Cuando el sistema este apagado, encender la lampara roja Q12.
// Rungs: 3 | Ramas paralelas: 1 | Variables: 6

export const program = {
  "metadata": {
    "project_id": "import_20260604115711",
    "name": "Enclavamiento_Lamparas",
    "version": "1.0.0",
    "plc_target": {
      "ip": "192.168.1.100",
      "port": 502,
      "unit_id": 1
    },
    "scan_time_ms": 100,
    "_explicacion": "Al presionar I1 se setea la marca M1; la rama paralela mantiene M1 activa después de soltar I1. Mientras M1=1 la lámpara verde Q10 está encendida. Si se pulsa I2 o ocurre emergencia I8, M1 se resetea y la lámpara verde se apaga, encendiéndose la lámpara roja Q12.",
    "_implementacion": "Crear la marca %M1 en la tabla de marcas → Programar el Renglón 1 con contactos XIC %I1, XIO %I2, XIO %I8 y bobina OTE %M1; añadir una rama paralela con contacto XIC %M1 → Programar el Renglón 2 con contacto XIC %M1 y salida OTE %Q10 → Programar el Renglón 3 con contacto XIO %M1 y salida OTE %Q12 → Descargar y probar la lógica en el PLC",
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
    "Q0.10": {
      "symbol": "Q0_10",
      "type": "BOOL",
      "modbus": {
        "fn": "write_coil",
        "address": null
      },
      "comment": "Salida — %Q10"
    },
    "Q0.12": {
      "symbol": "Q0_12",
      "type": "BOOL",
      "modbus": {
        "fn": "write_coil",
        "address": null
      },
      "comment": "Salida — %Q12"
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
      "comment": "Latch de encendido con auto‑retención",
      "network": [
        {
          "row": 0,
          "elements": [
            {
              "id": "e20260604115711r0f0c0",
              "type": "contact_no",
              "address": "I0.1",
              "pos": {
                "col": 0
              }
            },
            {
              "id": "e20260604115711r0f0c1",
              "type": "contact_nc",
              "address": "I0.2",
              "pos": {
                "col": 1
              }
            },
            {
              "id": "e20260604115711r0f0c2",
              "type": "contact_nc",
              "address": "I0.8",
              "pos": {
                "col": 2
              }
            },
            {
              "id": "e20260604115711r0f0c3",
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
              "id": "e20260604115711r0f1c0",
              "type": "contact_no",
              "address": "M0.1",
              "pos": {
                "col": 0
              }
            }
          ]
        }
      ]
    },
    {
      "id": 2,
      "enabled": true,
      "comment": "Encender lámpara verde mientras M1 está activa",
      "network": [
        {
          "row": 0,
          "elements": [
            {
              "id": "e20260604115711r1f0c0",
              "type": "contact_no",
              "address": "M0.1",
              "pos": {
                "col": 0
              }
            },
            {
              "id": "e20260604115711r1f0c1",
              "type": "coil",
              "address": "Q0.10",
              "pos": {
                "col": 1
              },
              "coil_type": "output"
            }
          ]
        }
      ]
    },
    {
      "id": 3,
      "enabled": true,
      "comment": "Encender lámpara roja cuando el sistema está apagado",
      "network": [
        {
          "row": 0,
          "elements": [
            {
              "id": "e20260604115711r2f0c0",
              "type": "contact_nc",
              "address": "M0.1",
              "pos": {
                "col": 0
              }
            },
            {
              "id": "e20260604115711r2f0c1",
              "type": "coil",
              "address": "Q0.12",
              "pos": {
                "col": 1
              },
              "coil_type": "output"
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
