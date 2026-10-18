-- Catálogo de tenants (empresas cliente). Cada tenant tiene su propia base
-- SQL Server y su propia base MongoDB.
CREATE TABLE dbo.tenant (
    id                     UNIQUEIDENTIFIER NOT NULL
        CONSTRAINT PK_tenant PRIMARY KEY
        CONSTRAINT DF_tenant_id DEFAULT NEWSEQUENTIALID(),
    slug                   NVARCHAR(40)     NOT NULL CONSTRAINT UQ_tenant_slug UNIQUE,
    nombre                 NVARCHAR(150)    NOT NULL,
    base_sqlserver         NVARCHAR(128)    NOT NULL,
    base_mongodb           NVARCHAR(64)     NOT NULL,
    auth0_organizacion_id  NVARCHAR(64)     NULL,
    estado                 NVARCHAR(20)     NOT NULL
        CONSTRAINT DF_tenant_estado DEFAULT 'aprovisionando'
        CONSTRAINT CK_tenant_estado
            CHECK (estado IN ('aprovisionando', 'activo', 'suspendido', 'error')),
    detalle_error          NVARCHAR(1000)   NULL,
    creado_en              DATETIME2(0)     NOT NULL
        CONSTRAINT DF_tenant_creado_en DEFAULT SYSUTCDATETIME(),
    actualizado_en         DATETIME2(0)     NOT NULL
        CONSTRAINT DF_tenant_actualizado_en DEFAULT SYSUTCDATETIME()
);
