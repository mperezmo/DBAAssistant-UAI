/*
    DistribuidoraDemo · 3. Problemas de DBA (a propósito)

    Deja en la base problemas típicos para probar Buenas prácticas, Monitoreo y
    Remediaciones. Cada uno está descripto en el README (qué debería detectar la app).
    No forma parte del documento funcional: es la "deuda técnica" del sistema anterior.
*/
SET NOCOUNT ON;
SET XACT_ABORT ON;
GO

USE DistribuidoraDemo;
GO

-- 1. Heap: los movimientos de stock quedan sin clave primaria ni índice agrupado.
ALTER TABLE dbo.tbl_mov_stk DROP CONSTRAINT PK_tbl_mov_stk;
GO

-- 2. Claves foráneas sin índice (los JOIN y los borrados en cascada recorren toda la tabla).
--    Además de estas dos, tbl_fac_cab.cod_cli nunca tuvo índice.
DROP INDEX IX_pedidos_detalle_producto ON dbo.pedidos_detalle;
DROP INDEX IX_tbl_cob_factura ON dbo.tbl_cob;
GO

-- 3. Tipo de dato obsoleto (TEXT) heredado del sistema anterior.
ALTER TABLE dbo.tbl_fac_cab ADD obs_int TEXT NULL;
GO
UPDATE dbo.tbl_fac_cab
SET obs_int = 'Cliente pidió reenviar la factura por correo.'
WHERE nro_fac % 97 = 0;
GO

-- 4. Índice fragmentado: bitácora técnica con clave agrupada GUID (NEWID) insertada al azar.
CREATE TABLE dbo.tbl_log_aud (
    id_log      UNIQUEIDENTIFIER NOT NULL
        CONSTRAINT DF_tbl_log_aud_id DEFAULT NEWID()
        CONSTRAINT PK_tbl_log_aud PRIMARY KEY CLUSTERED,
    fec         DATETIME2(0)  NOT NULL,
    usr         VARCHAR(20)   NOT NULL,
    tabla       VARCHAR(30)   NOT NULL,
    accion      CHAR(1)       NOT NULL,
    detalle     NVARCHAR(400) NOT NULL
);
GO

DECLARE @lote INT = 0;
WHILE @lote < 12
BEGIN
    INSERT INTO dbo.tbl_log_aud (fec, usr, tabla, accion, detalle)
    SELECT TOP (10000)
           DATEADD(MINUTE, -ABS(CHECKSUM(NEWID())) % 1000000, '2026-11-23T20:00:00'),
           CASE ABS(CHECKSUM(NEWID())) % 4 WHEN 0 THEN 'SIST' WHEN 1 THEN 'JPEREZ' WHEN 2 THEN 'MGARCIA' ELSE 'WEB' END,
           CASE ABS(CHECKSUM(NEWID())) % 4 WHEN 0 THEN 'pedidos' WHEN 1 THEN 'tbl_fac_cab' WHEN 2 THEN 'clientes' ELSE 'stk_act' END,
           CASE ABS(CHECKSUM(NEWID())) % 3 WHEN 0 THEN 'I' WHEN 1 THEN 'U' ELSE 'D' END,
           REPLICATE(N'Registro de auditoría del sistema anterior. ', 4)
    FROM sys.all_columns AS a CROSS JOIN sys.all_columns AS b;
    SET @lote += 1;
END;
GO

-- 5. Consulta lenta típica: función escalar por fila y filtros que no pueden usar índices.
CREATE OR ALTER FUNCTION dbo.fn_saldo_fac (@nro_fac INT)
RETURNS DECIMAL(14, 2)
AS
BEGIN
    DECLARE @saldo DECIMAL(14, 2);
    SELECT @saldo = f.imp_tot - ISNULL((SELECT SUM(c.imp_cob) FROM dbo.tbl_cob AS c WHERE c.nro_fac = f.nro_fac), 0)
    FROM dbo.tbl_fac_cab AS f
    WHERE f.nro_fac = @nro_fac;
    RETURN @saldo;
END;
GO

CREATE OR ALTER PROCEDURE dbo.sp_rpt_deuda_cli
AS
BEGIN
    SET NOCOUNT ON;
    SELECT c.razon_social, saldo = SUM(dbo.fn_saldo_fac(f.nro_fac))
    FROM dbo.tbl_fac_cab AS f
    JOIN dbo.clientes AS c ON c.id_cliente = f.cod_cli
    WHERE YEAR(f.fec_emi) >= 2025
      AND CONVERT(VARCHAR(10), f.cod_est_fac) IN ('1', '2')
    GROUP BY c.razon_social
    ORDER BY saldo DESC;
END;
GO

PRINT 'Problemas de DBA de DistribuidoraDemo aplicados.';
GO
