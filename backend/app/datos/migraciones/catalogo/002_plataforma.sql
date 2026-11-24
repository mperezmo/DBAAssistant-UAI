-- Plataforma: operadores (administran las empresas, no entran a sus datos) e invitaciones
-- para que una empresa nueva se registre sola.

-- Configuración única de la plataforma: la organización de Auth0 de los operadores.
CREATE TABLE dbo.plataforma (
    id                     TINYINT          NOT NULL
        CONSTRAINT PK_plataforma PRIMARY KEY
        CONSTRAINT CK_plataforma_unica CHECK (id = 1),
    auth0_organizacion_id  NVARCHAR(64)     NULL
);
GO

INSERT INTO dbo.plataforma (id) VALUES (1);
GO

CREATE TABLE dbo.operador (
    id                UNIQUEIDENTIFIER NOT NULL
        CONSTRAINT PK_operador PRIMARY KEY
        CONSTRAINT DF_operador_id DEFAULT NEWSEQUENTIALID(),
    email             NVARCHAR(254)    NOT NULL CONSTRAINT UQ_operador_email UNIQUE,
    nombre            NVARCHAR(150)    NULL,
    -- invitado: dado de alta, todavía no ingresó; baja: sin acceso.
    estado            NVARCHAR(20)     NOT NULL
        CONSTRAINT DF_operador_estado DEFAULT 'invitado'
        CONSTRAINT CK_operador_estado CHECK (estado IN ('invitado', 'activo', 'baja')),
    auth0_usuario_id  NVARCHAR(128)    NULL,
    creado_por        NVARCHAR(254)    NOT NULL,
    creado_en         DATETIME2(0)     NOT NULL
        CONSTRAINT DF_operador_creado_en DEFAULT SYSUTCDATETIME(),
    ultimo_ingreso    DATETIME2(0)     NULL,
    baja_en           DATETIME2(0)     NULL
);
GO

-- Invitación para registrar una empresa. Vence a las 24 h de enviarse o copiarse el link;
-- "vencida" no se guarda: es una pendiente con vence_en en el pasado.
CREATE TABLE dbo.invitacion_empresa (
    id                UNIQUEIDENTIFIER NOT NULL
        CONSTRAINT PK_invitacion_empresa PRIMARY KEY
        CONSTRAINT DF_invitacion_empresa_id DEFAULT NEWSEQUENTIALID(),
    email             NVARCHAR(254)    NOT NULL,
    estado            NVARCHAR(20)     NOT NULL
        CONSTRAINT DF_invitacion_empresa_estado DEFAULT 'pendiente'
        CONSTRAINT CK_invitacion_empresa_estado
            CHECK (estado IN ('pendiente', 'en_curso', 'usada', 'revocada')),
    creada_por        NVARCHAR(254)    NOT NULL,
    creada_en         DATETIME2(0)     NOT NULL
        CONSTRAINT DF_invitacion_empresa_creada_en DEFAULT SYSUTCDATETIME(),
    vence_en          DATETIME2(0)     NOT NULL,
    -- Código de verificación enviado por correo: solo su hash, con vencimiento e intentos.
    codigo_hash       CHAR(64)         NULL,
    codigo_vence_en   DATETIME2(0)     NULL,
    codigo_intentos   TINYINT          NOT NULL
        CONSTRAINT DF_invitacion_empresa_codigo_intentos DEFAULT 0,
    tenant_id         UNIQUEIDENTIFIER NULL
        CONSTRAINT FK_invitacion_empresa_tenant REFERENCES dbo.tenant (id),
    usada_en          DATETIME2(0)     NULL,
    revocada_en       DATETIME2(0)     NULL
);
GO

-- Una sola invitación abierta por email.
CREATE UNIQUE INDEX UX_invitacion_empresa_abierta
    ON dbo.invitacion_empresa (email)
    WHERE estado IN ('pendiente', 'en_curso');
GO

ALTER TABLE dbo.tenant ADD
    -- Email de quien registró la empresa (su primer Administrador).
    email_administrador  NVARCHAR(254) NULL;
