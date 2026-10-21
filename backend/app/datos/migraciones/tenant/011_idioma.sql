-- Idioma de la interfaz elegido por cada usuario (NULL: el del navegador).
ALTER TABLE dbo.usuario ADD idioma NVARCHAR(5) NULL;
GO

-- Textos de la alerta como clave de traducción y parámetros (JSON), para mostrarla en
-- el idioma de quien la ve. Las columnas titulo y descripcion conservan el texto en
-- español para las búsquedas y las alertas anteriores a este cambio.
ALTER TABLE dbo.alerta ADD textos NVARCHAR(MAX) NULL;
GO

ALTER TABLE dbo.alerta_desestimada ADD textos NVARCHAR(MAX) NULL;
