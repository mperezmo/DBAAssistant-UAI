-- Idioma elegido por cada operador para la consola y las sesiones de soporte (NULL: el
-- del navegador), como el de los usuarios de las empresas.
ALTER TABLE dbo.operador ADD
    idioma NVARCHAR(5) NULL
        CONSTRAINT CK_operador_idioma CHECK (idioma IN ('es', 'en'));
