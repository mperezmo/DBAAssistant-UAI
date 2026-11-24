/*
    DistribuidoraDemo · 5. Carga de trabajo (en la instancia DESTINO)

    Las estadísticas de consultas y de índices faltantes viven en la memoria de la
    instancia: no viajan en el backup y se pierden al reiniciar SQL Server. Este script
    ejecuta varias veces las consultas lentas para que Monitoreo ("Consultas más
    costosas") y Buenas prácticas ("Índices sugeridos") tengan algo que mostrar.
    Tarda alrededor de un minuto. Se puede volver a correr cuando haga falta.
*/
SET NOCOUNT ON;
GO

USE DistribuidoraDemo;
GO

DECLARE @i INT = 0, @cliente INT, @producto INT, @texto NVARCHAR(40), @total DECIMAL(18, 2);
DECLARE @deuda TABLE (razon_social NVARCHAR(120), saldo DECIMAL(14, 2));
WHILE @i < 25
BEGIN
    -- Reporte de deuda con función escalar por fila (el resultado se descarta).
    INSERT INTO @deuda EXEC dbo.sp_rpt_deuda_cli;
    DELETE FROM @deuda;

    -- Facturas de un cliente en un período: no hay índice por cliente (cod_cli).
    SET @cliente = 1 + (@i * 37) % 800;
    SELECT @total = SUM(imp_tot)
    FROM dbo.tbl_fac_cab
    WHERE cod_cli = @cliente AND fec_emi >= '2026-01-01';

    -- Ventas de un producto: la FK pedidos_detalle.id_producto no tiene índice.
    SET @producto = 1 + (@i * 13) % 250;
    SELECT @total = SUM(d.cant * d.precio_unit)
    FROM dbo.pedidos_detalle AS d
    JOIN dbo.pedidos AS p ON p.id_pedido = d.id_pedido
    WHERE d.id_producto = @producto AND p.cod_est <> 9;

    -- Búsqueda por texto con comodín al principio (no puede usar índices).
    SET @texto = CASE @i % 4 WHEN 0 THEN N'%Esquina%' WHEN 1 THEN N'%Sol%' WHEN 2 THEN N'%Roble%' ELSE N'%Plaza%' END;
    SELECT @total = COUNT(*) FROM dbo.clientes WHERE razon_social LIKE @texto;

    -- Movimientos de stock de un producto en una sucursal: tbl_mov_stk es un heap.
    SELECT @total = SUM(cant)
    FROM dbo.tbl_mov_stk
    WHERE cod_prod = @producto AND cod_suc = 1 + @i % 5 AND fec_mov >= '2026-06-01';

    SET @i += 1;
END;
GO

PRINT 'Carga de trabajo generada.';
GO
