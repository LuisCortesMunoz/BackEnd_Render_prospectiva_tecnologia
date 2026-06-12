// Generado : openai/gpt-oss-120b | 2026-06-04 17:52:06
// Consulta : Dame un programa para que al presionar un boton, se encienda la lampara amarilla
// Rungs: 1 | Ramas paralelas: 0 | Variables: 2

export const program = {
  "metadata": {
    "project_id": "import_20260604175206",
    "name": "EncenderLamparaAmarilla",
    "version": "1.0.0",
    "plc_target": {
      "ip": "192.168.1.100",
      "port": 502,
      "unit_id": 1
    },
    "scan_time_ms": 100,
    "_explicacion": "Al presionar el botón %I1 (NA) el contacto XIC se cierra y la bobina OTE activa la salida %Q11, encendiendo la lámpara amarilla.",
    "_implementacion": "Abrir Cscape 10.2 y crear un nuevo proyecto para XL4/ XC1E5 -> Insertar una red (renglón) en el editor ladder -> Colocar un contacto XIC y asignarle %I1 -> Colocar una bobina OTE y asignarle %Q11 -> Descargar el programa al PLC",
    "_python_modbus": "from pymodbus.client.sync import ModbusTcpClient\nclient=ModbusTcpClient('192.168.1.100',port=502)\nclient.connect()\n# Escribir coil 10 (correspondiente a %Q11) como ON (1)\nclient.write_coil(10,True)\nclient.close()"
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
    }
  },
  "rungs": [
    {
      "id": 1,
      "enabled": true,
      "comment": "Enciende la lámpara amarilla cuando se pulsa el botón de arranque",
      "network": [
        {
          "row": 0,
          "elements": [
            {
              "id": "e20260604175206r0f0c0",
              "type": "contact_no",
              "address": "I0.1",
              "pos": {
                "col": 0
              }
            },
            {
              "id": "e20260604175206r0f0c1",
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
