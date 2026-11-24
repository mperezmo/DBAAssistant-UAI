-- Permisos por ambiente (CU-001-002-C). Por defecto un usuario tiene todo lo que su rol
-- permite en sus ambientes habilitados; acá se guarda solo lo que el Administrador le quitó.
-- Así, un cambio de rol suma o recorta solo lo que corresponde sin perder las restricciones.
-- La tabla es temporal, como el resto de la configuración (historia en historia.*).
CREATE TABLE dbo.usuario_restriccion (
    usuario_id UNIQUEIDENTIFIER NOT NULL
        CONSTRAINT FK_usuario_restriccion_usuario REFERENCES dbo.usuario (id) ON DELETE CASCADE,
    tenant_id  UNIQUEIDENTIFIER NOT NULL,
    ambiente   NVARCHAR(10)     NOT NULL
        CONSTRAINT CK_usuario_restriccion_ambiente CHECK (ambiente IN ('DEV', 'TESTING', 'PROD')),
    accion     NVARCHAR(40)     NOT NULL
        CONSTRAINT CK_usuario_restriccion_accion CHECK (accion IN (
            'modificar', 'promover', 'ejecutar_remediaciones', 'autorizar_remediaciones',
            'aplicar_buenas_practicas', 'configurar_alertas', 'atender_alertas')),
    valido_desde DATETIME2 GENERATED ALWAYS AS ROW START HIDDEN NOT NULL
        CONSTRAINT DF_usuario_restriccion_valido_desde DEFAULT SYSUTCDATETIME(),
    valido_hasta DATETIME2 GENERATED ALWAYS AS ROW END HIDDEN NOT NULL
        CONSTRAINT DF_usuario_restriccion_valido_hasta
        DEFAULT CONVERT(DATETIME2, '9999-12-31 23:59:59.9999999'),
    PERIOD FOR SYSTEM_TIME (valido_desde, valido_hasta),
    CONSTRAINT PK_usuario_restriccion PRIMARY KEY (usuario_id, ambiente, accion)
)
WITH (SYSTEM_VERSIONING = ON (
    HISTORY_TABLE = historia.usuario_restriccion, HISTORY_RETENTION_PERIOD = 5 YEARS));
