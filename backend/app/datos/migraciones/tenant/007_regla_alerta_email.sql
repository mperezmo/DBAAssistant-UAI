-- Canal de correo para las reglas de alerta (además del push).
ALTER TABLE dbo.regla_alerta ADD email BIT NOT NULL
    CONSTRAINT DF_regla_alerta_email DEFAULT 0;
