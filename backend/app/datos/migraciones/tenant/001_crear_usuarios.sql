-- Usuarios del tenant. La identidad y el MFA los gestiona Auth0; acá se guarda
-- el rol y los ambientes habilitados. Toda tabla lleva tenant_id como segunda
-- barrera de aislamiento además de la base propia del tenant.
CREATE TABLE dbo.usuario (
    id               UNIQUEIDENTIFIER NOT NULL
        CONSTRAINT PK_usuario PRIMARY KEY
        CONSTRAINT DF_usuario_id DEFAULT NEWSEQUENTIALID(),
    tenant_id        UNIQUEIDENTIFIER NOT NULL,
    email            NVARCHAR(254)    NOT NULL,
    nombre           NVARCHAR(150)    NULL,
    auth0_usuario_id NVARCHAR(128)    NULL,
    rol              NVARCHAR(20)     NOT NULL
        CONSTRAINT CK_usuario_rol CHECK (rol IN ('ADMINISTRADOR', 'DBA', 'CONSULTA')),
    estado           NVARCHAR(20)     NOT NULL
        CONSTRAINT DF_usuario_estado DEFAULT 'invitado'
        CONSTRAINT CK_usuario_estado CHECK (estado IN ('invitado', 'activo', 'inactivo')),
    creado_en        DATETIME2(0)     NOT NULL
        CONSTRAINT DF_usuario_creado_en DEFAULT SYSUTCDATETIME(),
    actualizado_en   DATETIME2(0)     NOT NULL
        CONSTRAINT DF_usuario_actualizado_en DEFAULT SYSUTCDATETIME(),
    CONSTRAINT UQ_usuario_tenant_email UNIQUE (tenant_id, email)
);
GO

CREATE UNIQUE INDEX UX_usuario_auth0
    ON dbo.usuario (auth0_usuario_id)
    WHERE auth0_usuario_id IS NOT NULL;
GO

-- Ambientes en los que el usuario puede operar (CU-001-000-A).
CREATE TABLE dbo.usuario_ambiente (
    usuario_id UNIQUEIDENTIFIER NOT NULL
        CONSTRAINT FK_usuario_ambiente_usuario REFERENCES dbo.usuario (id) ON DELETE CASCADE,
    tenant_id  UNIQUEIDENTIFIER NOT NULL,
    ambiente   NVARCHAR(10)     NOT NULL
        CONSTRAINT CK_usuario_ambiente_ambiente CHECK (ambiente IN ('DEV', 'TESTING', 'PROD')),
    CONSTRAINT PK_usuario_ambiente PRIMARY KEY (usuario_id, ambiente)
);
