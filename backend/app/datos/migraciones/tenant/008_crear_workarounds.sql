-- Ventana de mantenimiento por conexión: las tareas pesadas (rebuild de índices,
-- estadísticas) fuera de ella requieren la autorización de otro usuario.
ALTER TABLE dbo.conexion ADD
    ventana_inicio  TIME(0)       NULL,
    ventana_fin     TIME(0)       NULL,
    zona_horaria    NVARCHAR(64)  NOT NULL
        CONSTRAINT DF_conexion_zona_horaria DEFAULT 'America/Argentina/Buenos_Aires';
GO

-- Workarounds propios (CU-000-005). Los integrados se definen en el código.
CREATE TABLE dbo.workaround (
    id                UNIQUEIDENTIFIER NOT NULL
        CONSTRAINT PK_workaround PRIMARY KEY
        CONSTRAINT DF_workaround_id DEFAULT NEWSEQUENTIALID(),
    tenant_id         UNIQUEIDENTIFIER NOT NULL,
    nombre            NVARCHAR(100)    NOT NULL,
    descripcion       NVARCHAR(500)    NOT NULL,
    categoria         NVARCHAR(20)     NOT NULL
        CONSTRAINT CK_workaround_categoria
        CHECK (categoria IN ('performance', 'espacio', 'mantenimiento', 'servicio')),
    criticidad        NVARCHAR(10)     NOT NULL
        CONSTRAINT CK_workaround_criticidad CHECK (criticidad IN ('info', 'warning', 'critical')),
    ambientes         NVARCHAR(30)     NOT NULL,  -- ambientes target, separados por coma
    roles             NVARCHAR(40)     NOT NULL,  -- roles que pueden ejecutarlo
    transaccional     BIT              NOT NULL,
    condicion         NVARCHAR(300)    NULL,      -- condición de aplicabilidad (texto)
    version_actual    INT              NOT NULL,
    -- Última versión validada en Sandbox: la única que se ejecuta en PROD.
    version_aprobada  INT              NULL,
    creado_por        NVARCHAR(254)    NOT NULL,
    creado_en         DATETIME2(0)     NOT NULL
        CONSTRAINT DF_workaround_creado_en DEFAULT SYSUTCDATETIME(),
    eliminado_en      DATETIME2(0)     NULL
);
GO

CREATE UNIQUE INDEX UX_workaround_nombre
    ON dbo.workaround (tenant_id, nombre)
    WHERE eliminado_en IS NULL;
GO

-- Historial de versiones (CU-000-005-B): cada modificación crea una nueva.
CREATE TABLE dbo.workaround_version (
    workaround_id   UNIQUEIDENTIFIER NOT NULL
        CONSTRAINT FK_workaround_version_workaround REFERENCES dbo.workaround (id),
    version         INT              NOT NULL,
    pasos           NVARCHAR(MAX)    NOT NULL,  -- JSON: lista de lotes T-SQL
    creada_por      NVARCHAR(254)    NOT NULL,
    creada_en       DATETIME2(0)     NOT NULL
        CONSTRAINT DF_workaround_version_creada_en DEFAULT SYSUTCDATETIME(),
    validada_en     DATETIME2(0)     NULL,
    validacion      NVARCHAR(MAX)    NULL,      -- JSON: resultado de la validación en Sandbox
    CONSTRAINT PK_workaround_version PRIMARY KEY (workaround_id, version)
);
GO

-- Ejecuciones (integrados y propios), con el resultado de cada paso.
CREATE TABLE dbo.ejecucion_workaround (
    id                  INT IDENTITY(1, 1) NOT NULL
        CONSTRAINT PK_ejecucion_workaround PRIMARY KEY,
    tenant_id           UNIQUEIDENTIFIER NOT NULL,
    workaround          NVARCHAR(60)     NOT NULL,  -- id del integrado o del propio
    workaround_nombre   NVARCHAR(100)    NOT NULL,
    version             INT              NULL,
    conexion_id         UNIQUEIDENTIFIER NOT NULL,
    conexion_alias      NVARCHAR(60)     NOT NULL,
    ambiente            NVARCHAR(10)     NOT NULL,
    base                NVARCHAR(128)    NULL,
    parametros          NVARCHAR(MAX)    NOT NULL,
    alerta_id           INT              NULL,
    origen              NVARCHAR(10)     NOT NULL
        CONSTRAINT CK_ejecucion_workaround_origen CHECK (origen IN ('web', 'mobile', 'automatica')),
    estado              NVARCHAR(10)     NOT NULL
        CONSTRAINT CK_ejecucion_workaround_estado
        CHECK (estado IN ('en_curso', 'exitosa', 'fallida', 'revertida')),
    pasos               NVARCHAR(MAX)    NOT NULL,
    error               NVARCHAR(1000)   NULL,
    ejecutada_por       NVARCHAR(254)    NOT NULL,
    autorizada_por      NVARCHAR(254)    NULL,
    iniciada_en         DATETIME2(0)     NOT NULL
        CONSTRAINT DF_ejecucion_workaround_iniciada_en DEFAULT SYSUTCDATETIME(),
    finalizada_en       DATETIME2(0)     NULL
);
GO

CREATE INDEX IX_ejecucion_workaround ON dbo.ejecucion_workaround (tenant_id, workaround, iniciada_en DESC);
GO

-- Pedidos de autorización para ejecutar fuera de la ventana de mantenimiento.
CREATE TABLE dbo.autorizacion_workaround (
    id              UNIQUEIDENTIFIER NOT NULL
        CONSTRAINT PK_autorizacion_workaround PRIMARY KEY
        CONSTRAINT DF_autorizacion_workaround_id DEFAULT NEWSEQUENTIALID(),
    tenant_id       UNIQUEIDENTIFIER NOT NULL,
    workaround      NVARCHAR(60)     NOT NULL,
    workaround_nombre NVARCHAR(100)  NOT NULL,
    conexion_id     UNIQUEIDENTIFIER NOT NULL
        CONSTRAINT FK_autorizacion_workaround_conexion REFERENCES dbo.conexion (id) ON DELETE CASCADE,
    conexion_alias  NVARCHAR(60)     NOT NULL,
    motivo          NVARCHAR(500)    NOT NULL,
    solicitada_por  NVARCHAR(254)    NOT NULL,
    solicitada_en   DATETIME2(0)     NOT NULL
        CONSTRAINT DF_autorizacion_workaround_solicitada_en DEFAULT SYSUTCDATETIME(),
    estado          NVARCHAR(10)     NOT NULL
        CONSTRAINT DF_autorizacion_workaround_estado DEFAULT 'pendiente'
        CONSTRAINT CK_autorizacion_workaround_estado
        CHECK (estado IN ('pendiente', 'autorizada', 'rechazada', 'usada')),
    resuelta_por    NVARCHAR(254)    NULL,
    resuelta_en     DATETIME2(0)     NULL,
    -- Pendiente: vence el pedido. Autorizada: vence la habilitación para ejecutar.
    vence_en        DATETIME2(0)     NOT NULL
);
GO

-- Ejecución automática de integrados aptos ante eventos (configurada por un DBA).
CREATE TABLE dbo.automatizacion_workaround (
    id            UNIQUEIDENTIFIER NOT NULL
        CONSTRAINT PK_automatizacion_workaround PRIMARY KEY
        CONSTRAINT DF_automatizacion_workaround_id DEFAULT NEWSEQUENTIALID(),
    tenant_id     UNIQUEIDENTIFIER NOT NULL,
    evento        NVARCHAR(20)     NOT NULL
        CONSTRAINT CK_automatizacion_workaround_evento CHECK (evento IN ('log', 'instancia')),
    workaround    NVARCHAR(60)     NOT NULL,
    -- NULL: todas las conexiones.
    conexion_id   UNIQUEIDENTIFIER NULL
        CONSTRAINT FK_automatizacion_workaround_conexion REFERENCES dbo.conexion (id) ON DELETE CASCADE,
    activa        BIT              NOT NULL,
    creada_por    NVARCHAR(254)    NOT NULL,
    creada_en     DATETIME2(0)     NOT NULL
        CONSTRAINT DF_automatizacion_workaround_creada_en DEFAULT SYSUTCDATETIME()
);
