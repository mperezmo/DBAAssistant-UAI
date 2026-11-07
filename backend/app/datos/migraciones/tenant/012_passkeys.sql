-- Passkeys (WebAuthn) de cada usuario: confirman las acciones sensibles con la biometría
-- del dispositivo (huella, rostro o PIN) sin salir de la aplicación. La clave privada
-- nunca sale del dispositivo; acá se guarda solo la pública.
CREATE TABLE dbo.passkey (
    id              UNIQUEIDENTIFIER NOT NULL
        CONSTRAINT PK_passkey PRIMARY KEY
        CONSTRAINT DF_passkey_id DEFAULT NEWSEQUENTIALID(),
    tenant_id       UNIQUEIDENTIFIER NOT NULL,
    usuario_id      UNIQUEIDENTIFIER NOT NULL
        CONSTRAINT FK_passkey_usuario REFERENCES dbo.usuario (id) ON DELETE CASCADE,
    credencial_id   NVARCHAR(700)    NOT NULL,  -- base64url
    clave_publica   VARBINARY(MAX)   NOT NULL,
    contador        BIGINT           NOT NULL,
    nombre          NVARCHAR(100)    NOT NULL,
    creada_en       DATETIME2(0)     NOT NULL
        CONSTRAINT DF_passkey_creada_en DEFAULT SYSUTCDATETIME(),
    ultimo_uso      DATETIME2(0)     NULL,
    CONSTRAINT UQ_passkey_credencial UNIQUE (tenant_id, credencial_id)
);
