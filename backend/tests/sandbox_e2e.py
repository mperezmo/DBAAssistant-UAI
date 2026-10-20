"""Prueba de punta a punta del servicio Sandbox, ejecutada en la CI dentro de su imagen.

Requiere el servicio escuchando en 127.0.0.1:8100 con acceso a Docker y un SQL Server
de origen en 127.0.0.1,14330. No forma parte de pytest: la invoca el workflow.
"""

import os
import sys
import time

import httpx2
import pyodbc

ORIGEN = {"servidor": "127.0.0.1,14330", "usuario": "sa", "password": os.environ["ORIGEN_PASSWORD"]}
CABECERA = {"X-Sandbox-Token": os.environ["SECRETO_SANDBOX_TOKEN"]}
URL = "http://127.0.0.1:8100/sandboxes/ci"


def preparar_origen() -> None:
    cadena = (
        "DRIVER={ODBC Driver 18 for SQL Server};SERVER=127.0.0.1,14330;UID=sa;"
        f"PWD={{{ORIGEN['password']}}};Encrypt=yes;TrustServerCertificate=yes"
    )
    with pyodbc.connect(cadena, autocommit=True) as conexion:
        conexion.cursor().execute("IF DB_ID('Ventas') IS NULL CREATE DATABASE Ventas")
    with pyodbc.connect(cadena.replace("SERVER=", "DATABASE=Ventas;SERVER="), autocommit=True) as c:
        c.cursor().execute(
            "IF OBJECT_ID('dbo.factura') IS NULL "
            "CREATE TABLE dbo.factura (id INT PRIMARY KEY, total DECIMAL(10,2), estado INT)"
        )
        c.cursor().execute(
            "IF NOT EXISTS (SELECT 1 FROM dbo.factura) INSERT dbo.factura VALUES (1, 10, 0)"
        )


def principal() -> None:
    preparar_origen()
    with httpx2.Client(headers=CABECERA, timeout=1800) as http:
        assert http.get(URL, headers={"X-Sandbox-Token": "otro"}).status_code == 401
        inicio = time.monotonic()
        estado = http.post(URL).raise_for_status().json()
        assert estado["estado"] == "running", estado
        print(f"Sandbox listo en {time.monotonic() - inicio:.0f} s")

        sincronizacion = http.post(
            f"{URL}/sincronizar", json={"origen": ORIGEN, "bases": ["Ventas"]}
        )
        resultado = sincronizacion.raise_for_status().json()["bases"][0]
        assert resultado["estado"] == "ok", resultado
        alineadas = http.post(f"{URL}/huellas", json={"origen": ORIGEN, "bases": ["Ventas"]}).json()
        assert alineadas == {"alineadas": {"Ventas": True}}, alineadas

        ejecucion = http.post(
            f"{URL}/ejecutar",
            json={
                "base": "Ventas",
                "lotes": ["UPDATE dbo.factura SET estado = 1", "SELECT * FROM dbo.factura"],
            },
        ).json()
        assert ejecucion["estado"] == "ok" and ejecucion["plan"]["operador"], ejecucion
        # Solo estructura: la tabla del Sandbox no tiene filas.
        assert ejecucion["muestra"]["filas"] == [], ejecucion
        sintaxis = http.post(
            f"{URL}/ejecutar", json={"base": "Ventas", "lotes": ["SELECT * FROM dbo.inexistente"]}
        ).json()
        assert sintaxis["estado"] in ("sintaxis", "error"), sintaxis
    print("Sandbox OK")


if __name__ == "__main__":
    try:
        principal()
    except AssertionError as error:
        print("Falla:", error, file=sys.stderr)
        raise
