// Generado : openai/gpt-oss-120b | 2026-06-04 11:55:28
// Consulta : Quiero contador que al presionar un boton I1 cuente hasta 10 y al llegar a 10 se active la salida Q11
// Rungs: 2 | Ramas paralelas: 0 | Variables: 4

export const program = {
  "metadata": {
    "project_id": "import_20260604115528",
    "name": "Contador_10_pulsos",
    "version": "1.0.0",
    "plc_target": {
      "ip": "192.168.1.100",
      "port": 502,
      "unit_id": 1
    },
    "scan_time_ms": 100,
    "_explicacion": "Cada vez que se pulsa %I1 el contador %R0010 incrementa; al alcanzar 10 el bit %M0010 se activa y enciende %Q11.",
    "_implementacion": "Crear registro %R0010 y marca %M0010 → Insertar bloque CTU en renglon 1 y OTE en renglon 2 → Configurar preset 10 en CTU → Descargar al PLC",
    "_python_modbus": "import pymodbus.client.sync as modbus;client=modbus.ModbusTcpClient('192.168.1.100');client.connect();# Leer contador client.read_holding_registers(0,1,unit=1);# Escribir salida client.write_coil(11,True);client.close()"
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
    "Q0.11": {
      "symbol": "Q0_11",
      "type": "BOOL",
      "modbus": {
        "fn": "write_coil",
        "address": null
      },
      "comment": "Salida — %Q11"
    },
    "M0.10": {
      "symbol": "M0_10",
      "type": "BOOL",
      "modbus": {
        "fn": "internal",
        "address": null
      },
      "comment": "Marca — %M0010"
    },
    "MW10": {
      "symbol": "MW10",
      "type": "BOOL",
      "modbus": {
        "fn": "holding_reg",
        "address": null
      },
      "comment": "Registro — %R0010"
    }
  },
  "rungs": [
    {
      "id": 1,
      "enabled": true,
      "comment": "Contador ascendente CTU, incrementa con cada pulso de %I1",
      "network": [
        {
          "row": 0,
          "elements": [
            {
              "id": "e20260604115528r0f0c0",
              "type": "contact_no",
              "address": "I0.1",
              "pos": {
                "col": 0
              }
            },
            {
              "id": "e20260604115528r0f0c1",
              "type": "block_ctu",
              "address": "MW10",
              "pos": {
                "col": 1
              },
              "params": {
                "preset": 10
              }
            },
            {
              "id": "e20260604115528r0f0c2",
              "type": "coil",
              "address": "M0.10",
              "pos": {
                "col": 2
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
      "comment": "Activar salida Q11 cuando contador llega a 10",
      "network": [
        {
          "row": 0,
          "elements": [
            {
              "id": "e20260604115528r1f0c0",
              "type": "contact_no",
              "address": "M0.10",
              "pos": {
                "col": 0
              }
            },
            {
              "id": "e20260604115528r1f0c1",
              "type": "coil",
              "address": "Q0.11",
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
