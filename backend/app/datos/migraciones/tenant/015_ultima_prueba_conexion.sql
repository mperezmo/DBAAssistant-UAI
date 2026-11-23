-- Resultado de la última prueba de cada conexión, para mostrarla en la lista ("Sin probar",
-- "OK · hace 2 min" o "Falló"). La tabla es temporal: el historial de pruebas queda en
-- historia.conexion.
ALTER TABLE dbo.conexion ADD
    ultima_prueba_en DATETIME2(0) NULL,
    ultima_prueba_ok BIT          NULL;
