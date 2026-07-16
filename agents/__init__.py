"""Paquete de agentes del backend LadderVoice (arquitectura de agente).

Ver ARQUITECTURA_AGENTE.md. Componentes por fase:
- Fase 1: clarify  -> deteccion de prompts ambiguos / slots faltantes.
- Fases siguientes: planner, experts, tools, policy, validators.

Todo es ADITIVO: no reemplaza el flujo actual de /generar-logica, solo lo
enriquece de forma retrocompatible.
"""
