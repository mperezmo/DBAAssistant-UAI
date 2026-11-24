/*
    DistribuidoraDemo · Borra la base del SQL Server del docker (una vez restaurada en la
    instancia destino).
*/
USE master;
GO
IF DB_ID(N'DistribuidoraDemo') IS NOT NULL
BEGIN
    ALTER DATABASE DistribuidoraDemo SET SINGLE_USER WITH ROLLBACK IMMEDIATE;
    DROP DATABASE DistribuidoraDemo;
END;
PRINT 'DistribuidoraDemo borrada de esta instancia.';
GO
