-- Política de backups por conexión: horas máximas sin backup full y minutos máximos sin
-- backup de log (bases con recuperación FULL o BULK_LOGGED). Sin fila, la conexión usa
-- los valores por defecto definidos en el código (24 horas y 60 minutos).
CREATE TABLE dbo.politica_backup (
    conexion_id     UNIQUEIDENTIFIER NOT NULL
        CONSTRAINT FK_politica_backup_conexion REFERENCES dbo.conexion (id) ON DELETE CASCADE,
    tenant_id       UNIQUEIDENTIFIER NOT NULL,
    habilitada      BIT              NOT NULL,
    full_horas      INT              NOT NULL
        CONSTRAINT CK_politica_backup_full CHECK (full_horas BETWEEN 1 AND 744),
    log_minutos     INT              NOT NULL
        CONSTRAINT CK_politica_backup_log CHECK (log_minutos BETWEEN 5 AND 1440),
    actualizado_en  DATETIME2(0)     NOT NULL
        CONSTRAINT DF_politica_backup_actualizado_en DEFAULT SYSUTCDATETIME(),
    CONSTRAINT PK_politica_backup PRIMARY KEY (conexion_id)
);
