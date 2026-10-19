-- Conexiones a instancias SQL Server del cliente (CU-000-000). Una conexión es una
-- instancia; las bases se eligen al usarla. Las contraseñas se guardan cifradas.
CREATE TABLE dbo.conexion (
    id                       UNIQUEIDENTIFIER NOT NULL
        CONSTRAINT PK_conexion PRIMARY KEY
        CONSTRAINT DF_conexion_id DEFAULT NEWSEQUENTIALID(),
    tenant_id                UNIQUEIDENTIFIER NOT NULL,
    alias                    NVARCHAR(60)     NOT NULL,
    host                     NVARCHAR(255)    NOT NULL,
    puerto                   INT              NULL,
    instancia                NVARCHAR(128)    NULL,
    ambiente                 NVARCHAR(10)     NOT NULL
        CONSTRAINT CK_conexion_ambiente CHECK (ambiente IN ('DEV', 'TESTING', 'PROD')),
    usuario_sql              NVARCHAR(128)    NOT NULL,
    password_sql_cifrada     NVARCHAR(MAX)    NOT NULL,
    -- Acceso al servidor Windows (WinRM) para ver o iniciar el servicio de SQL Server.
    winrm_usuario            NVARCHAR(128)    NULL,
    winrm_password_cifrada   NVARCHAR(MAX)    NULL,
    winrm_puerto             INT              NULL,
    winrm_https              BIT              NOT NULL
        CONSTRAINT DF_conexion_winrm_https DEFAULT 0,
    creado_en                DATETIME2(0)     NOT NULL
        CONSTRAINT DF_conexion_creado_en DEFAULT SYSUTCDATETIME(),
    actualizado_en           DATETIME2(0)     NOT NULL
        CONSTRAINT DF_conexion_actualizado_en DEFAULT SYSUTCDATETIME(),
    CONSTRAINT UQ_conexion_tenant_alias UNIQUE (tenant_id, alias)
);
