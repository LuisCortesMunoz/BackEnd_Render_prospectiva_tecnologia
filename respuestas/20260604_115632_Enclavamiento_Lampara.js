// Generado : openai/gpt-oss-120b | 2026-06-04 11:56:32
// Consulta : Quiero un enclavamiento: encender la lampara verde Q10 con el boton de arranque I1 y que se mantenga encendida al soltar el boton. Apagarla con el paro I2Cuando el sistema este apagado, encender la lampara roja Q12.
// Rungs: 3 | Ramas paralelas: 1 | Variables: 6

export const program = {
  "metadata": {
    "project_id": "import_20260604115632",
    "name": "Enclavamiento_Lampara",
    "version": "1.0.0",
    "plc_target": {
      "ip": "192.168.1.100",
      "port": 502,
      "unit_id": 1
    },
    "scan_time_ms": 100,
    "_explicacion": "El programa usa una marca interna %M1 como latch; al pulsar I1 mientras I2 e I8 están libres se pone %M1 y la lámpara verde Q10 se enciende y se mantiene. Un paro (I2) o emergencia (I8) restablece %M1, apagando la verde y encendiendo la roja Q12.",
    "_implementacion": "Crear marca interna %M1 → Renglón 1: colocar contactos XIC %I1, XIO %I2, XIO %I8 y bobina OTE %M1; añadir rama paralela con contacto XIC %M1 → Renglón 2: colocar contacto XIC %M1 y bobina OTE %Q10 → Renglón 3: colocar contacto XIO %M1 y bobina OTE %Q12 → Descargar programa al PLC y probar.",
    "_python_modbus": "import pymodbus.client.sync as sync\nclient=sync.ModbusTcpClient('192.168.1.100',port=502)\nclient.connect()\n# Encender verde (coil 10) -> address 9 (0‑based)\nclient.write_coil(9,True)\n# Apagar verde, encender roja (coil 12) -> address 11\nclient.write_coil(9,False)\nclient.write_coil(11,True)\nclient.close()"
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
      "comment": "Lazo de enclavamiento (auto‑retención) usando %M1",
      "network": [
        {
          "row": 0,
          "elements": [
            {
              "id": "e20260604115632r0f0c0",
              "type": "contact_no",
              "address": "I0.1",
              "pos": {
                "col": 0
              }
            },
            {
              "id": "e20260604115632r0f0c1",
              "type": "contact_nc",
              "address": "I0.2",
              "pos": {
                "col": 1
              }
            },
            {
              "id": "e20260604115632r0f0c2",
              "type": "contact_nc",
              "address": "I0.8",
              "pos": {
                "col": 2
              }
            },
            {
              "id": "e20260604115632r0f0c3",
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
              "id": "e20260604115632r0f1c0",
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
      "comment": "Salida lámpara verde",
      "network": [
        {
          "row": 0,
          "elements": [
            {
              "id": "e20260604115632r1f0c0",
              "type": "contact_no",
              "address": "M0.1",
              "pos": {
                "col": 0
              }
            },
            {
              "id": "e20260604115632r1f0c1",
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
      "comment": "Salida lámpara roja (sistema apagado)",
      "network": [
        {
          "row": 0,
          "elements": [
            {
              "id": "e20260604115632r2f0c0",
              "type": "contact_nc",
              "address": "M0.1",
              "pos": {
                "col": 0
              }
            },
            {
              "id": "e20260604115632r2f0c1",
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
