/*
    DistribuidoraDemo · 1. Estructura

    Base de prueba de una distribuidora mayorista ficticia (Distribuidora Sur S.A.) para
    evaluar DBA Assistant. Convive un modelo nuevo, con nombres claros, con tablas
    heredadas de un sistema anterior (nombres abreviados y códigos numéricos): el
    documento funcional explica qué significa cada una.

    ATENCIÓN: borra y vuelve a crear la base DistribuidoraDemo.
    Compatible con SQL Server 2016 o posterior.
*/
SET NOCOUNT ON;
SET XACT_ABORT ON;
GO

USE master;
GO

IF DB_ID(N'DistribuidoraDemo') IS NOT NULL
BEGIN
    ALTER DATABASE DistribuidoraDemo SET SINGLE_USER WITH ROLLBACK IMMEDIATE;
    DROP DATABASE DistribuidoraDemo;
END;
GO

CREATE DATABASE DistribuidoraDemo;
GO

-- Recuperación completa, como una base productiva (habilita backups de log).
ALTER DATABASE DistribuidoraDemo SET RECOVERY FULL;
-- Query Store: historia de consultas dentro de la base (viaja con el backup).
ALTER DATABASE DistribuidoraDemo SET QUERY_STORE = ON;
ALTER DATABASE DistribuidoraDemo SET QUERY_STORE (OPERATION_MODE = READ_WRITE);
GO

USE DistribuidoraDemo;
GO

/* ---------------------------------------------------------------------------------
   Modelo comercial (nombres claros)
   --------------------------------------------------------------------------------- */

CREATE TABLE dbo.sucursales (
    id_sucursal     SMALLINT      NOT NULL CONSTRAINT PK_sucursales PRIMARY KEY,
    nombre          NVARCHAR(60)  NOT NULL,
    ciudad          NVARCHAR(60)  NOT NULL,
    provincia       NVARCHAR(40)  NOT NULL,
    fecha_apertura  DATE          NOT NULL,
    activa          BIT           NOT NULL CONSTRAINT DF_sucursales_activa DEFAULT 1
);

CREATE TABLE dbo.vendedores (
    id_vendedor     INT           NOT NULL CONSTRAINT PK_vendedores PRIMARY KEY,
    nombre          NVARCHAR(40)  NOT NULL,
    apellido        NVARCHAR(40)  NOT NULL,
    email           NVARCHAR(120) NOT NULL,
    id_sucursal     SMALLINT      NOT NULL
        CONSTRAINT FK_vendedores_sucursales REFERENCES dbo.sucursales (id_sucursal),
    fecha_ingreso   DATE          NOT NULL,
    fecha_egreso    DATE          NULL,
    comision_pct    DECIMAL(4, 2) NOT NULL
);

CREATE TABLE dbo.clientes (
    id_cliente      INT           NOT NULL CONSTRAINT PK_clientes PRIMARY KEY,
    razon_social    NVARCHAR(120) NOT NULL,
    cuit            CHAR(13)      NOT NULL,
    email           NVARCHAR(120) NULL,
    telefono        VARCHAR(20)   NULL,
    direccion       NVARCHAR(120) NULL,
    ciudad          NVARCHAR(60)  NOT NULL,
    provincia       NVARCHAR(40)  NOT NULL,
    segmento        NVARCHAR(20)  NOT NULL
        CONSTRAINT CK_clientes_segmento
            CHECK (segmento IN (N'Minorista', N'Mayorista', N'Gastronomía', N'Institucional')),
    cond_iva        CHAR(2)       NOT NULL
        CONSTRAINT CK_clientes_cond_iva CHECK (cond_iva IN ('RI', 'MT', 'CF', 'EX')),
    id_vendedor     INT           NOT NULL
        CONSTRAINT FK_clientes_vendedores REFERENCES dbo.vendedores (id_vendedor),
    fecha_alta      DATE          NOT NULL,
    limite_credito  DECIMAL(14, 2) NOT NULL,
    activo          BIT           NOT NULL CONSTRAINT DF_clientes_activo DEFAULT 1
);
CREATE INDEX IX_clientes_vendedor ON dbo.clientes (id_vendedor);

CREATE TABLE dbo.categorias (
    id_categoria        SMALLINT     NOT NULL CONSTRAINT PK_categorias PRIMARY KEY,
    nombre              NVARCHAR(60) NOT NULL,
    id_categoria_padre  SMALLINT     NULL
        CONSTRAINT FK_categorias_padre REFERENCES dbo.categorias (id_categoria)
);

CREATE TABLE dbo.productos (
    id_producto     INT           NOT NULL CONSTRAINT PK_productos PRIMARY KEY,
    sku             VARCHAR(20)   NOT NULL CONSTRAINT UQ_productos_sku UNIQUE,
    nombre          NVARCHAR(120) NOT NULL,
    id_categoria    SMALLINT      NOT NULL
        CONSTRAINT FK_productos_categorias REFERENCES dbo.categorias (id_categoria),
    marca           NVARCHAR(40)  NOT NULL,
    unidad_medida   VARCHAR(5)    NOT NULL,
    precio_lista    DECIMAL(12, 2) NOT NULL,
    costo_ult       DECIMAL(12, 2) NOT NULL,
    activo          BIT           NOT NULL CONSTRAINT DF_productos_activo DEFAULT 1,
    fecha_alta      DATE          NOT NULL
);
CREATE INDEX IX_productos_categoria ON dbo.productos (id_categoria);

CREATE TABLE dbo.pedidos (
    id_pedido       INT           NOT NULL CONSTRAINT PK_pedidos PRIMARY KEY,
    nro_pedido      VARCHAR(15)   NOT NULL CONSTRAINT UQ_pedidos_nro UNIQUE,
    id_cliente      INT           NOT NULL
        CONSTRAINT FK_pedidos_clientes REFERENCES dbo.clientes (id_cliente),
    id_vendedor     INT           NULL
        CONSTRAINT FK_pedidos_vendedores REFERENCES dbo.vendedores (id_vendedor),
    id_sucursal     SMALLINT      NOT NULL
        CONSTRAINT FK_pedidos_sucursales REFERENCES dbo.sucursales (id_sucursal),
    fecha_pedido    DATETIME2(0)  NOT NULL,
    fec_ent_est     DATE          NOT NULL,
    cod_est         TINYINT       NOT NULL
        CONSTRAINT CK_pedidos_cod_est CHECK (cod_est IN (1, 2, 3, 4, 9)),
    canal           CHAR(1)       NOT NULL
        CONSTRAINT CK_pedidos_canal CHECK (canal IN ('M', 'T', 'W', 'V')),
    obs             NVARCHAR(200) NULL
);
CREATE INDEX IX_pedidos_cliente ON dbo.pedidos (id_cliente);
CREATE INDEX IX_pedidos_vendedor ON dbo.pedidos (id_vendedor);
CREATE INDEX IX_pedidos_fecha ON dbo.pedidos (fecha_pedido) INCLUDE (cod_est, id_sucursal);

CREATE TABLE dbo.pedidos_detalle (
    id_pedido       INT           NOT NULL
        CONSTRAINT FK_pedidos_detalle_pedidos REFERENCES dbo.pedidos (id_pedido),
    nro_linea       SMALLINT      NOT NULL,
    id_producto     INT           NOT NULL
        CONSTRAINT FK_pedidos_detalle_productos REFERENCES dbo.productos (id_producto),
    cant            DECIMAL(10, 2) NOT NULL,
    precio_unit     DECIMAL(12, 2) NOT NULL,
    dto_pct         DECIMAL(5, 2) NOT NULL CONSTRAINT DF_pedidos_detalle_dto DEFAULT 0,
    CONSTRAINT PK_pedidos_detalle PRIMARY KEY (id_pedido, nro_linea)
);
CREATE INDEX IX_pedidos_detalle_producto ON dbo.pedidos_detalle (id_producto);

CREATE TABLE dbo.devoluciones (
    id_devolucion   INT           NOT NULL CONSTRAINT PK_devoluciones PRIMARY KEY,
    nro_fac         INT           NOT NULL,
    id_producto     INT           NOT NULL
        CONSTRAINT FK_devoluciones_productos REFERENCES dbo.productos (id_producto),
    cantidad        DECIMAL(10, 2) NOT NULL,
    motivo_cod      CHAR(2)       NOT NULL
        CONSTRAINT CK_devoluciones_motivo CHECK (motivo_cod IN ('RT', 'VC', 'ER', 'NC')),
    fecha           DATE          NOT NULL,
    importe         DECIMAL(14, 2) NOT NULL
);
CREATE INDEX IX_devoluciones_factura ON dbo.devoluciones (nro_fac);

/* ---------------------------------------------------------------------------------
   Tablas heredadas del sistema anterior (nombres abreviados y códigos)
   --------------------------------------------------------------------------------- */

-- Cabecera de facturas.
CREATE TABLE dbo.tbl_fac_cab (
    nro_fac         INT           NOT NULL CONSTRAINT PK_tbl_fac_cab PRIMARY KEY,
    tip_cbte        CHAR(1)       NOT NULL CONSTRAINT CK_tbl_fac_cab_tip CHECK (tip_cbte IN ('A', 'B')),
    pto_vta         SMALLINT      NOT NULL,
    id_ped          INT           NOT NULL
        CONSTRAINT FK_tbl_fac_cab_pedidos REFERENCES dbo.pedidos (id_pedido),
    cod_cli         INT           NOT NULL
        CONSTRAINT FK_tbl_fac_cab_clientes REFERENCES dbo.clientes (id_cliente),
    fec_emi         DATE          NOT NULL,
    fec_vto         DATE          NOT NULL,
    imp_net         DECIMAL(14, 2) NOT NULL,
    imp_iva         DECIMAL(14, 2) NOT NULL,
    imp_tot         DECIMAL(14, 2) NOT NULL,
    cod_est_fac     TINYINT       NOT NULL
        CONSTRAINT CK_tbl_fac_cab_est CHECK (cod_est_fac IN (1, 2, 3, 8))
);
CREATE UNIQUE INDEX UX_tbl_fac_cab_pedido ON dbo.tbl_fac_cab (id_ped);
CREATE INDEX IX_tbl_fac_cab_emision ON dbo.tbl_fac_cab (fec_emi);

ALTER TABLE dbo.devoluciones ADD
    CONSTRAINT FK_devoluciones_facturas FOREIGN KEY (nro_fac) REFERENCES dbo.tbl_fac_cab (nro_fac);

-- Cobranzas (pagos de facturas).
CREATE TABLE dbo.tbl_cob (
    id_cob          INT           NOT NULL CONSTRAINT PK_tbl_cob PRIMARY KEY,
    nro_fac         INT           NOT NULL
        CONSTRAINT FK_tbl_cob_facturas REFERENCES dbo.tbl_fac_cab (nro_fac),
    fec_cob         DATE          NOT NULL,
    imp_cob         DECIMAL(14, 2) NOT NULL,
    med_pag         TINYINT       NOT NULL CONSTRAINT CK_tbl_cob_med CHECK (med_pag IN (1, 2, 3, 4)),
    nro_ref         VARCHAR(30)   NULL
);
CREATE INDEX IX_tbl_cob_factura ON dbo.tbl_cob (nro_fac);

-- Movimientos de stock.
CREATE TABLE dbo.tbl_mov_stk (
    id_mov          BIGINT        NOT NULL CONSTRAINT PK_tbl_mov_stk PRIMARY KEY,
    cod_prod        INT           NOT NULL
        CONSTRAINT FK_tbl_mov_stk_productos REFERENCES dbo.productos (id_producto),
    cod_suc         SMALLINT      NOT NULL
        CONSTRAINT FK_tbl_mov_stk_sucursales REFERENCES dbo.sucursales (id_sucursal),
    fec_mov         DATETIME2(0)  NOT NULL,
    tip_mov         CHAR(2)       NOT NULL
        CONSTRAINT CK_tbl_mov_stk_tip CHECK (tip_mov IN ('IN', 'VT', 'DV', 'AJ', 'TR')),
    cant            DECIMAL(10, 2) NOT NULL,
    ref_doc         VARCHAR(20)   NULL,
    usr_alt         VARCHAR(20)   NOT NULL
);
CREATE INDEX IX_tbl_mov_stk_producto ON dbo.tbl_mov_stk (cod_prod, cod_suc, fec_mov);

-- Stock actual por producto y sucursal.
CREATE TABLE dbo.stk_act (
    cod_prod        INT           NOT NULL
        CONSTRAINT FK_stk_act_productos REFERENCES dbo.productos (id_producto),
    cod_suc         SMALLINT      NOT NULL
        CONSTRAINT FK_stk_act_sucursales REFERENCES dbo.sucursales (id_sucursal),
    cant_disp       DECIMAL(10, 2) NOT NULL,
    cant_min        DECIMAL(10, 2) NOT NULL,
    fec_ult_act     DATETIME2(0)  NOT NULL,
    CONSTRAINT PK_stk_act PRIMARY KEY (cod_prod, cod_suc)
);

-- Parámetros generales del sistema.
CREATE TABLE dbo.prm_gral (
    cod_prm         VARCHAR(20)   NOT NULL CONSTRAINT PK_prm_gral PRIMARY KEY,
    val_prm         NVARCHAR(100) NOT NULL
);
GO

PRINT 'Estructura de DistribuidoraDemo creada.';
GO
