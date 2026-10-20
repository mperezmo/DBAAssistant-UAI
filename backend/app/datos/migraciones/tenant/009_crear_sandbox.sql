-- Sandbox (CU-000-002): un contenedor por servidor de origen; la estructura se
-- sincroniza a diario a la hora local configurada en cada conexión.
ALTER TABLE dbo.conexion ADD sandbox_sincronizacion TIME(0) NOT NULL
    CONSTRAINT DF_conexion_sandbox_sincronizacion DEFAULT '03:00';
GO

CREATE TABLE dbo.sandbox (
    tenant_id        UNIQUEIDENTIFIER NOT NULL,
    clave            NVARCHAR(60)     NOT NULL
        CONSTRAINT PK_sandbox PRIMARY KEY,
    host             NVARCHAR(255)    NOT NULL,
    estado           NVARCHAR(15)     NOT NULL
        CONSTRAINT CK_sandbox_estado CHECK (estado IN ('sincronizando', 'listo', 'error')),
    sincronizado_en  DATETIME2(0)     NULL,
    bases            NVARCHAR(MAX)    NOT NULL,  -- JSON: resultado por base
    error            NVARCHAR(1000)   NULL,
    actualizado_en   DATETIME2(0)     NOT NULL
        CONSTRAINT DF_sandbox_actualizado_en DEFAULT SYSUTCDATETIME()
);
