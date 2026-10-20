-- Configuración de métricas de monitoreo por conexión (CU-000-003-B). Sin fila, la
-- métrica usa los valores por defecto definidos en el código.
CREATE TABLE dbo.umbral_metrica (
    tenant_id       UNIQUEIDENTIFIER NOT NULL,
    conexion_id     UNIQUEIDENTIFIER NOT NULL
        CONSTRAINT FK_umbral_metrica_conexion REFERENCES dbo.conexion (id) ON DELETE CASCADE,
    metrica         NVARCHAR(30)     NOT NULL,
    habilitada      BIT              NOT NULL,
    warning         FLOAT            NULL,
    critical        FLOAT            NULL,
    actualizado_en  DATETIME2(0)     NOT NULL
        CONSTRAINT DF_umbral_metrica_actualizado_en DEFAULT SYSUTCDATETIME(),
    CONSTRAINT PK_umbral_metrica PRIMARY KEY (conexion_id, metrica),
    CONSTRAINT CK_umbral_metrica_orden CHECK (critical > warning)
);
