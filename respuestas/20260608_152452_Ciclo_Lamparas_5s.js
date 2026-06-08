// Generado : openai/gpt-oss-120b | 2026-06-08 15:24:52
// Consulta : Necesito un programa para cuando presiones un boton se cilce dos lampareas, a 5 segundosSe enciende una, al pasar el tiempo se apaga y se enciede la otra
// Rungs: 5 | Ramas paralelas: 6 | Variables: 5

export const program = {
  "metadata": {
    "project_id": "import_20260608152452",
    "name": "Ciclo_Lamparas_5s",
    "version": "1.0.0",
    "plc_target": {
      "ip": "192.168.1.100",
      "port": 502,
      "unit_id": 1
    },
    "scan_time_ms": 100,
    "_explicacion": "",
    "_implementacion": "",
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
      "comment": ""
    },
    "M0.1": {
      "symbol": "M0_1",
      "type": "BOOL",
      "modbus": {
        "fn": "internal",
        "address": null
      },
      "comment": ""
    },
    "MW1": {
      "symbol": "MW1",
      "type": "INT",
      "modbus": {
        "fn": "holding_reg",
        "address": null
      },
      "comment": ""
    },
    "Q0.10": {
      "symbol": "Q0_10",
      "type": "BOOL",
      "modbus": {
        "fn": "write_coil",
        "address": null
      },
      "comment": ""
    },
    "Q0.11": {
      "symbol": "Q0_11",
      "type": "BOOL",
      "modbus": {
        "fn": "write_coil",
        "address": null
      },
      "comment": ""
    }
  },
  "rungs": [
    {
      "id": 1,
      "enabled": true,
      "comment": "Detectar pulsación del botón y establecer marca de inicio",
      "network": [
        {
          "row": 0,
          "elements": [
            {
              "id": "e20260608152452r0f0c0",
              "type": "contact_no",
              "address": "I0.1",
              "pos": {
                "col": 0
              }
            }
          ]
        },
        {
          "row": 1,
          "elements": [
            {
              "id": "e20260608152452r0f1c0",
              "type": "coil",
              "address": "M0.1",
              "pos": {
                "col": 0
              },
              "coil_type": "output"
            }
          ]
        }
      ]
    },
    {
      "id": 2,
      "enabled": true,
      "comment": "Iniciar temporizador de 5 s cuando la marca está activa",
      "network": [
        {
          "row": 0,
          "elements": [
            {
              "id": "e20260608152452r1f0c0",
              "type": "contact_no",
              "address": "M0.1",
              "pos": {
                "col": 0
              }
            }
          ]
        },
        {
          "row": 1,
          "elements": [
            {
              "id": "e20260608152452r1f1c0",
              "type": "block_ton",
              "address": "MW1",
              "pos": {
                "col": 0
              },
              "params": {
                "preset_ms": 1000
              }
            }
          ]
        }
      ]
    },
    {
      "id": 3,
      "enabled": true,
      "comment": "Encender lámpara verde mientras el temporizador no ha terminado",
      "network": [
        {
          "row": 0,
          "elements": [
            {
              "id": "e20260608152452r2f0c0",
              "type": "contact_no",
              "address": "M0.1",
              "pos": {
                "col": 0
              }
            }
          ]
        },
        {
          "row": 1,
          "elements": [
            {
              "id": "e20260608152452r2f1c0",
              "type": "contact_nc",
              "address": "MW1",
              "pos": {
                "col": 0
              }
            }
          ]
        },
        {
          "row": 2,
          "elements": [
            {
              "id": "e20260608152452r2f2c0",
              "type": "coil",
              "address": "Q0.10",
              "pos": {
                "col": 0
              },
              "coil_type": "output"
            }
          ]
        }
      ]
    },
    {
      "id": 4,
      "enabled": true,
      "comment": "Encender lámpara amarilla cuando el temporizador finaliza",
      "network": [
        {
          "row": 0,
          "elements": [
            {
              "id": "e20260608152452r3f0c0",
              "type": "contact_no",
              "address": "MW1",
              "pos": {
                "col": 0
              }
            }
          ]
        },
        {
          "row": 1,
          "elements": [
            {
              "id": "e20260608152452r3f1c0",
              "type": "coil",
              "address": "Q0.11",
              "pos": {
                "col": 0
              },
              "coil_type": "output"
            }
          ]
        }
      ]
    },
    {
      "id": 5,
      "enabled": true,
      "comment": "Resetear marca de ciclo para permitir una nueva activación",
      "network": [
        {
          "row": 0,
          "elements": [
            {
              "id": "e20260608152452r4f0c0",
              "type": "contact_no",
              "address": "MW1",
              "pos": {
                "col": 0
              }
            }
          ]
        },
        {
          "row": 1,
          "elements": [
            {
              "id": "e20260608152452r4f1c0",
              "type": "coil_s",
              "address": "M0.1",
              "pos": {
                "col": 0
              },
              "coil_type": "set"
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
