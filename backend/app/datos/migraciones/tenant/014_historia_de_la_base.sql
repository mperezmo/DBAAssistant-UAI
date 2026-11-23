-- Historia de la base de la aplicación (Soporte): datos y estructura a través del tiempo.
--
-- Estructura: un trigger de base registra cada cambio de estructura (DDL) con su T-SQL,
-- quién lo hizo y desde qué programa, incluidos los hechos fuera de la aplicación.
-- Se crea primero para que la propia conversión a tablas temporales quede registrada.
--
-- Datos: tablas temporales con versión del sistema. SQL Server guarda cada versión
-- anterior de cada fila en el esquema "historia" durante 5 años (el mismo plazo que las
-- bitácoras). Las columnas del período son ocultas: no cambian los SELECT * ni los
-- INSERT existentes.
CREATE SCHEMA historia;
GO

CREATE TABLE dbo.historial_ddl (
    id           BIGINT IDENTITY(1, 1) NOT NULL CONSTRAINT PK_historial_ddl PRIMARY KEY,
    ts           DATETIME2(3)   NOT NULL CONSTRAINT DF_historial_ddl_ts DEFAULT SYSUTCDATETIME(),
    evento       NVARCHAR(100)  NOT NULL,
    esquema      NVARCHAR(128)  NULL,
    objeto       NVARCHAR(256)  NULL,
    tipo_objeto  NVARCHAR(60)   NULL,
    login        NVARCHAR(128)  NOT NULL,
    programa     NVARCHAR(128)  NULL,
    equipo       NVARCHAR(128)  NULL,
    tsql         NVARCHAR(MAX)  NULL
);
CREATE INDEX IX_historial_ddl_ts ON dbo.historial_ddl (ts DESC);
GO

-- Se ejecuta como dbo para que un login con permisos de DDL pero sin INSERT sobre la
-- tabla no vea fallar su cambio; ORIGINAL_LOGIN() conserva el login real.
CREATE TRIGGER trg_historial_ddl ON DATABASE
WITH EXECUTE AS 'dbo'
FOR DDL_DATABASE_LEVEL_EVENTS
AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @evento XML = EVENTDATA();
    INSERT INTO dbo.historial_ddl (evento, esquema, objeto, tipo_objeto, login, programa, equipo, tsql)
    VALUES (
        @evento.value('(/EVENT_INSTANCE/EventType)[1]', 'NVARCHAR(100)'),
        @evento.value('(/EVENT_INSTANCE/SchemaName)[1]', 'NVARCHAR(128)'),
        @evento.value('(/EVENT_INSTANCE/ObjectName)[1]', 'NVARCHAR(256)'),
        @evento.value('(/EVENT_INSTANCE/ObjectType)[1]', 'NVARCHAR(60)'),
        ORIGINAL_LOGIN(),
        APP_NAME(),
        HOST_NAME(),
        @evento.value('(/EVENT_INSTANCE/TSQLCommand/CommandText)[1]', 'NVARCHAR(MAX)')
    );
END;
GO

ALTER TABLE dbo.usuario ADD
    valido_desde DATETIME2 GENERATED ALWAYS AS ROW START HIDDEN NOT NULL
        CONSTRAINT DF_usuario_valido_desde DEFAULT SYSUTCDATETIME(),
    valido_hasta DATETIME2 GENERATED ALWAYS AS ROW END HIDDEN NOT NULL
        CONSTRAINT DF_usuario_valido_hasta DEFAULT CONVERT(DATETIME2, '9999-12-31 23:59:59.9999999'),
    PERIOD FOR SYSTEM_TIME (valido_desde, valido_hasta);
ALTER TABLE dbo.usuario SET (SYSTEM_VERSIONING = ON (
    HISTORY_TABLE = historia.usuario, HISTORY_RETENTION_PERIOD = 5 YEARS));
GO

ALTER TABLE dbo.usuario_ambiente ADD
    valido_desde DATETIME2 GENERATED ALWAYS AS ROW START HIDDEN NOT NULL
        CONSTRAINT DF_usuario_ambiente_valido_desde DEFAULT SYSUTCDATETIME(),
    valido_hasta DATETIME2 GENERATED ALWAYS AS ROW END HIDDEN NOT NULL
        CONSTRAINT DF_usuario_ambiente_valido_hasta DEFAULT CONVERT(DATETIME2, '9999-12-31 23:59:59.9999999'),
    PERIOD FOR SYSTEM_TIME (valido_desde, valido_hasta);
ALTER TABLE dbo.usuario_ambiente SET (SYSTEM_VERSIONING = ON (
    HISTORY_TABLE = historia.usuario_ambiente, HISTORY_RETENTION_PERIOD = 5 YEARS));
GO

ALTER TABLE dbo.conexion ADD
    valido_desde DATETIME2 GENERATED ALWAYS AS ROW START HIDDEN NOT NULL
        CONSTRAINT DF_conexion_valido_desde DEFAULT SYSUTCDATETIME(),
    valido_hasta DATETIME2 GENERATED ALWAYS AS ROW END HIDDEN NOT NULL
        CONSTRAINT DF_conexion_valido_hasta DEFAULT CONVERT(DATETIME2, '9999-12-31 23:59:59.9999999'),
    PERIOD FOR SYSTEM_TIME (valido_desde, valido_hasta);
ALTER TABLE dbo.conexion SET (SYSTEM_VERSIONING = ON (
    HISTORY_TABLE = historia.conexion, HISTORY_RETENTION_PERIOD = 5 YEARS));
GO

ALTER TABLE dbo.umbral_metrica ADD
    valido_desde DATETIME2 GENERATED ALWAYS AS ROW START HIDDEN NOT NULL
        CONSTRAINT DF_umbral_metrica_valido_desde DEFAULT SYSUTCDATETIME(),
    valido_hasta DATETIME2 GENERATED ALWAYS AS ROW END HIDDEN NOT NULL
        CONSTRAINT DF_umbral_metrica_valido_hasta DEFAULT CONVERT(DATETIME2, '9999-12-31 23:59:59.9999999'),
    PERIOD FOR SYSTEM_TIME (valido_desde, valido_hasta);
ALTER TABLE dbo.umbral_metrica SET (SYSTEM_VERSIONING = ON (
    HISTORY_TABLE = historia.umbral_metrica, HISTORY_RETENTION_PERIOD = 5 YEARS));
GO

ALTER TABLE dbo.regla_alerta ADD
    valido_desde DATETIME2 GENERATED ALWAYS AS ROW START HIDDEN NOT NULL
        CONSTRAINT DF_regla_alerta_valido_desde DEFAULT SYSUTCDATETIME(),
    valido_hasta DATETIME2 GENERATED ALWAYS AS ROW END HIDDEN NOT NULL
        CONSTRAINT DF_regla_alerta_valido_hasta DEFAULT CONVERT(DATETIME2, '9999-12-31 23:59:59.9999999'),
    PERIOD FOR SYSTEM_TIME (valido_desde, valido_hasta);
ALTER TABLE dbo.regla_alerta SET (SYSTEM_VERSIONING = ON (
    HISTORY_TABLE = historia.regla_alerta, HISTORY_RETENTION_PERIOD = 5 YEARS));
GO

ALTER TABLE dbo.politica_backup ADD
    valido_desde DATETIME2 GENERATED ALWAYS AS ROW START HIDDEN NOT NULL
        CONSTRAINT DF_politica_backup_valido_desde DEFAULT SYSUTCDATETIME(),
    valido_hasta DATETIME2 GENERATED ALWAYS AS ROW END HIDDEN NOT NULL
        CONSTRAINT DF_politica_backup_valido_hasta DEFAULT CONVERT(DATETIME2, '9999-12-31 23:59:59.9999999'),
    PERIOD FOR SYSTEM_TIME (valido_desde, valido_hasta);
ALTER TABLE dbo.politica_backup SET (SYSTEM_VERSIONING = ON (
    HISTORY_TABLE = historia.politica_backup, HISTORY_RETENTION_PERIOD = 5 YEARS));
GO

ALTER TABLE dbo.workaround ADD
    valido_desde DATETIME2 GENERATED ALWAYS AS ROW START HIDDEN NOT NULL
        CONSTRAINT DF_workaround_valido_desde DEFAULT SYSUTCDATETIME(),
    valido_hasta DATETIME2 GENERATED ALWAYS AS ROW END HIDDEN NOT NULL
        CONSTRAINT DF_workaround_valido_hasta DEFAULT CONVERT(DATETIME2, '9999-12-31 23:59:59.9999999'),
    PERIOD FOR SYSTEM_TIME (valido_desde, valido_hasta);
ALTER TABLE dbo.workaround SET (SYSTEM_VERSIONING = ON (
    HISTORY_TABLE = historia.workaround, HISTORY_RETENTION_PERIOD = 5 YEARS));
GO

ALTER TABLE dbo.automatizacion_workaround ADD
    valido_desde DATETIME2 GENERATED ALWAYS AS ROW START HIDDEN NOT NULL
        CONSTRAINT DF_automatizacion_workaround_valido_desde DEFAULT SYSUTCDATETIME(),
    valido_hasta DATETIME2 GENERATED ALWAYS AS ROW END HIDDEN NOT NULL
        CONSTRAINT DF_automatizacion_workaround_valido_hasta DEFAULT CONVERT(DATETIME2, '9999-12-31 23:59:59.9999999'),
    PERIOD FOR SYSTEM_TIME (valido_desde, valido_hasta);
ALTER TABLE dbo.automatizacion_workaround SET (SYSTEM_VERSIONING = ON (
    HISTORY_TABLE = historia.automatizacion_workaround, HISTORY_RETENTION_PERIOD = 5 YEARS));
GO

ALTER TABLE dbo.alerta_desestimada ADD
    valido_desde DATETIME2 GENERATED ALWAYS AS ROW START HIDDEN NOT NULL
        CONSTRAINT DF_alerta_desestimada_valido_desde DEFAULT SYSUTCDATETIME(),
    valido_hasta DATETIME2 GENERATED ALWAYS AS ROW END HIDDEN NOT NULL
        CONSTRAINT DF_alerta_desestimada_valido_hasta DEFAULT CONVERT(DATETIME2, '9999-12-31 23:59:59.9999999'),
    PERIOD FOR SYSTEM_TIME (valido_desde, valido_hasta);
ALTER TABLE dbo.alerta_desestimada SET (SYSTEM_VERSIONING = ON (
    HISTORY_TABLE = historia.alerta_desestimada, HISTORY_RETENTION_PERIOD = 5 YEARS));
