-- Condiciones desestimadas: una situación esperada en una instancia (por ejemplo, una
-- base offline a propósito) que no debe generar alertas mientras esté vigente.
CREATE TABLE dbo.alerta_desestimada (
    id               UNIQUEIDENTIFIER NOT NULL
        CONSTRAINT PK_alerta_desestimada PRIMARY KEY
        CONSTRAINT DF_alerta_desestimada_id DEFAULT NEWSEQUENTIALID(),
    tenant_id        UNIQUEIDENTIFIER NOT NULL,
    conexion_id      UNIQUEIDENTIFIER NOT NULL
        CONSTRAINT FK_alerta_desestimada_conexion REFERENCES dbo.conexion (id) ON DELETE CASCADE,
    clave            NVARCHAR(200)    NOT NULL,
    titulo           NVARCHAR(200)    NOT NULL,
    conexion_alias   NVARCHAR(60)     NOT NULL,
    motivo           NVARCHAR(500)    NOT NULL,
    -- NULL: sin vencimiento.
    hasta            DATETIME2(0)     NULL,
    creada_por       NVARCHAR(254)    NOT NULL,
    creada_en        DATETIME2(0)     NOT NULL
        CONSTRAINT DF_alerta_desestimada_creada_en DEFAULT SYSUTCDATETIME(),
    reactivada_por   NVARCHAR(254)    NULL,
    reactivada_en    DATETIME2(0)     NULL
);
GO

CREATE UNIQUE INDEX UX_alerta_desestimada_vigente
    ON dbo.alerta_desestimada (tenant_id, conexion_id, clave)
    WHERE reactivada_en IS NULL;
GO

ALTER TABLE dbo.alerta DROP CONSTRAINT CK_alerta_accion;
GO

ALTER TABLE dbo.alerta ADD CONSTRAINT CK_alerta_accion
    CHECK (accion IN ('manual', 'falsa_alarma', 'regla_eliminada', 'desestimada'));
