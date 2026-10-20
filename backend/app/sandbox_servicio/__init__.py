"""Servicio Sandbox: único componente con acceso al motor de Docker.

Crea y administra los contenedores SQL Server donde se validan consultas y scripts
sin riesgo (bloque "Sandbox Docker" del diagrama). No se expone a internet: lo usa
solo el backend, autenticándose con un token compartido.
"""
