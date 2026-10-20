-- Reglas de alerta propias (CU-000-004-A). Las alertas por umbrales del monitoreo y
-- los eventos fijos no necesitan regla. La baja es lógica (CU-000-004-D).
CREATE TABLE dbo.regla_alerta (
    id                  UNIQUEIDENTIFIER NOT NULL
        CONSTRAINT PK_regla_alerta PRIMARY KEY
        CONSTRAINT DF_regla_alerta_id DEFAULT NEWSEQUENTIALID(),
    tenant_id           UNIQUEIDENTIFIER NOT NULL,
    nombre              NVARCHAR(100)    NOT NULL,
    metrica             NVARCHAR(30)     NOT NULL,
    operador            NVARCHAR(2)      NOT NULL
        CONSTRAINT CK_regla_alerta_operador CHECK (operador IN ('>', '>=', '<', '<=')),
    valor               FLOAT            NOT NULL,
    severidad           NVARCHAR(10)     NOT NULL
        CONSTRAINT CK_regla_alerta_severidad CHECK (severidad IN ('info', 'warning', 'critical')),
    -- NULL: aplica a todas las conexiones del tenant.
    conexion_id         UNIQUEIDENTIFIER NULL
        CONSTRAINT FK_regla_alerta_conexion REFERENCES dbo.conexion (id) ON DELETE CASCADE,
    sostenida_segundos  INT              NOT NULL,
    destinatarios       NVARCHAR(100)    NOT NULL,  -- roles separados por coma
    push                BIT              NOT NULL,
    activa              BIT              NOT NULL
        CONSTRAINT DF_regla_alerta_activa DEFAULT 1,
    creada_por          NVARCHAR(254)    NOT NULL,
    creada_en           DATETIME2(0)     NOT NULL
        CONSTRAINT DF_regla_alerta_creada_en DEFAULT SYSUTCDATETIME(),
    eliminada_en        DATETIME2(0)     NULL
);
GO

CREATE UNIQUE INDEX UX_regla_alerta_nombre
    ON dbo.regla_alerta (tenant_id, nombre)
    WHERE eliminada_en IS NULL;
GO

-- Alertas disparadas. Conservan alias y servidor para que el historial siga legible
-- aunque la conexión se elimine.
CREATE TABLE dbo.alerta (
    id               INT IDENTITY(1, 1) NOT NULL
        CONSTRAINT PK_alerta PRIMARY KEY,
    tenant_id        UNIQUEIDENTIFIER NOT NULL,
    conexion_id      UNIQUEIDENTIFIER NOT NULL,
    conexion_alias   NVARCHAR(60)     NOT NULL,
    servidor         NVARCHAR(300)    NOT NULL,
    ambiente         NVARCHAR(10)     NOT NULL,
    origen           NVARCHAR(10)     NOT NULL
        CONSTRAINT CK_alerta_origen CHECK (origen IN ('umbral', 'evento', 'regla')),
    -- Identifica la condición: una sola alerta activa por conexión y clave.
    clave            NVARCHAR(200)    NOT NULL,
    regla_id         UNIQUEIDENTIFIER NULL,
    titulo           NVARCHAR(200)    NOT NULL,
    descripcion      NVARCHAR(500)    NOT NULL,
    severidad        NVARCHAR(10)     NOT NULL
        CONSTRAINT CK_alerta_severidad CHECK (severidad IN ('info', 'warning', 'critical')),
    metrica          NVARCHAR(30)     NULL,
    valor            FLOAT            NULL,
    umbral           FLOAT            NULL,
    unidad           NVARCHAR(10)     NULL,
    estado           NVARCHAR(10)     NOT NULL
        CONSTRAINT DF_alerta_estado DEFAULT 'activa'
        CONSTRAINT CK_alerta_estado CHECK (estado IN ('activa', 'atendida')),
    normalizada      BIT              NOT NULL
        CONSTRAINT DF_alerta_normalizada DEFAULT 0,
    ocurrencias      INT              NOT NULL
        CONSTRAINT DF_alerta_ocurrencias DEFAULT 1,
    disparada_en     DATETIME2(0)     NOT NULL
        CONSTRAINT DF_alerta_disparada_en DEFAULT SYSUTCDATETIME(),
    actualizada_en   DATETIME2(0)     NOT NULL
        CONSTRAINT DF_alerta_actualizada_en DEFAULT SYSUTCDATETIME(),
    normalizada_en   DATETIME2(0)     NULL,
    atendida_en      DATETIME2(0)     NULL,
    atendida_por     NVARCHAR(254)    NULL,
    accion           NVARCHAR(20)     NULL
        CONSTRAINT CK_alerta_accion CHECK (accion IN ('manual', 'falsa_alarma', 'regla_eliminada')),
    comentario       NVARCHAR(1000)   NULL
);
GO

CREATE UNIQUE INDEX UX_alerta_activa
    ON dbo.alerta (tenant_id, conexion_id, clave)
    WHERE estado = 'activa';
GO

CREATE INDEX IX_alerta_estado ON dbo.alerta (tenant_id, estado, disparada_en DESC);
GO

-- Dispositivos suscriptos a notificaciones push (Web Push, CU-000-004-E).
CREATE TABLE dbo.suscripcion_push (
    id           UNIQUEIDENTIFIER NOT NULL
        CONSTRAINT PK_suscripcion_push PRIMARY KEY
        CONSTRAINT DF_suscripcion_push_id DEFAULT NEWSEQUENTIALID(),
    tenant_id    UNIQUEIDENTIFIER NOT NULL,
    usuario_id   UNIQUEIDENTIFIER NOT NULL
        CONSTRAINT FK_suscripcion_push_usuario REFERENCES dbo.usuario (id) ON DELETE CASCADE,
    endpoint     NVARCHAR(800)    NOT NULL,
    p256dh       NVARCHAR(200)    NOT NULL,
    auth         NVARCHAR(100)    NOT NULL,
    agente       NVARCHAR(300)    NULL,
    creada_en    DATETIME2(0)     NOT NULL
        CONSTRAINT DF_suscripcion_push_creada_en DEFAULT SYSUTCDATETIME(),
    CONSTRAINT UQ_suscripcion_push_endpoint UNIQUE (endpoint)
);
