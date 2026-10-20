-- Ejecución automática ante una base no disponible (workaround "Poner online una base").
ALTER TABLE dbo.automatizacion_workaround DROP CONSTRAINT CK_automatizacion_workaround_evento;
GO

ALTER TABLE dbo.automatizacion_workaround ADD CONSTRAINT CK_automatizacion_workaround_evento
    CHECK (evento IN ('log', 'instancia', 'base'));
